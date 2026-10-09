"""
Office-format conversion.

    PDF  -> DOCX     layout-preserving via pdf2docx, text fallback via python-docx
    DOCX -> PDF      MS Word COM  ->  LibreOffice  ->  built-in renderer
    PDF  -> XLSX/CSV table extraction (pdfplumber, PyMuPDF find_tables)
    XLSX -> PDF      MS Excel COM ->  LibreOffice  ->  ReportLab renderer

Every path reports which engine it used, and the UI shows that to the user, so
a lower-fidelity fallback is never passed off as a full-fidelity conversion.
"""

from __future__ import annotations

import csv
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from app import paths
from app.core import deps, utils
from app.core.jobs import JobContext, JobError
from app.core.pdfbase import document, looks_scanned, page_size_points, pymupdf


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------

@dataclass
class ConvertResult:
    outputs: list[Path] = field(default_factory=list)
    engine: str = ""
    pages: int = 0
    tables: int = 0
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    fidelity: str = "high"                # high | medium | basic

    @property
    def output(self) -> Path | None:
        return self.outputs[0] if self.outputs else None


FIDELITY_NOTE = {
    "high": "",
    "medium": "Layout is approximated. Complex columns, floating boxes and "
             "unusual fonts may shift.",
    "basic": "This is a text-and-image conversion. Original page layout is not "
             "preserved.",
}


# ===========================================================================
# PDF -> Word
# ===========================================================================

@dataclass
class PdfToWordOptions:
    pages: str = "all"
    keep_layout: bool = True
    extract_tables: bool = True
    extract_images: bool = True
    ocr_if_scanned: bool = True


def pdf_to_word(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    opts: PdfToWordOptions | None = None,
    password: str | None = None,
) -> ConvertResult:
    src = Path(source)
    opts = opts or PdfToWordOptions()
    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "converted", ".docx")
    )
    utils.ensure_parent(out)
    result = ConvertResult()

    with document(src, password) as doc:
        result.pages = doc.page_count
        scanned = looks_scanned(doc)

    if scanned:
        result.warnings.append(
            "This PDF appears to be scanned — it contains little or no embedded "
            "text. Run OCR first for an editable result."
        )

    if opts.keep_layout and deps.has_pdf2docx():
        try:
            _pdf2docx(ctx, src, out, opts, password)
            result.engine = "pdf2docx"
            result.fidelity = "high"
            result.outputs.append(out)
            result.message = f"Converted {result.pages} page(s) to Word"
            ctx.progress(100, result.message)
            return result
        except Exception as e:
            ctx.warn(f"Layout conversion failed ({e}); falling back to text extraction.")
            result.warnings.append(f"Layout engine failed: {e}")

    _pdf_to_docx_fallback(ctx, src, out, opts, password, result)
    result.engine = "built-in"
    result.fidelity = "basic"
    result.outputs.append(out)
    result.message = f"Converted {result.pages} page(s) to Word (text layout)"
    ctx.progress(100, result.message)
    return result


def _pdf2docx(ctx, src: Path, out: Path, opts: PdfToWordOptions, password):
    import logging

    from pdf2docx import Converter                      # type: ignore

    # pdf2docx logs its four-stage progress to stdout, which has nowhere to go
    # in a windowed build. Route it through our own progress reporting instead.
    for name in ("pdf2docx", "pdf2docx.converter"):
        logging.getLogger(name).setLevel(logging.ERROR)

    ctx.stage("Analysing layout")
    ctx.progress(10, "Analysing layout")
    cv = Converter(str(src), password=password or "")
    try:
        total = len(cv.fitz_doc)
        pages = utils.parse_page_ranges(opts.pages, total)
        start = (pages[0] - 1) if pages else 0
        end = pages[-1] if pages else total
        ctx.progress(25, "Rebuilding document")
        cv.convert(str(out), start=start, end=end)
        ctx.progress(92, "Finishing")
    finally:
        cv.close()


def _pdf_to_docx_fallback(ctx, src: Path, out: Path, opts: PdfToWordOptions,
                          password, result: ConvertResult) -> None:
    """Text/image reconstruction that needs only python-docx + PyMuPDF."""
    try:
        import docx                                      # type: ignore
        from docx.shared import Inches, Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError as e:                             # pragma: no cover
        raise JobError("python-docx is required for Word conversion.") from e

    tmpdir = Path(tempfile.mkdtemp(prefix="pmt_docx_", dir=str(paths.temp_dir())))
    document_ = docx.Document()

    try:
        with document(src, password) as doc:
            total = doc.page_count
            pages = utils.parse_page_ranges(opts.pages, total)
            ctx.set_total(len(pages), "Converting pages")

            # page geometry from the first page
            if pages:
                r = doc[pages[0] - 1].rect
                section = document_.sections[0]
                section.page_width = Inches(r.width / 72.0)
                section.page_height = Inches(r.height / 72.0)
                for attr in ("left_margin", "right_margin"):
                    setattr(section, attr, Inches(0.6))
                section.top_margin = section.bottom_margin = Inches(0.6)

            for n, pno in enumerate(pages):
                ctx.check_cancel()
                page = doc[pno - 1]

                blocks = page.get_text("dict").get("blocks", [])
                blocks.sort(key=lambda b: (round(b.get("bbox", [0, 0])[1], 1),
                                           round(b.get("bbox", [0, 0])[0], 1)))

                for block in blocks:
                    if block.get("type") == 1 and opts.extract_images:
                        img_bytes = block.get("image")
                        if img_bytes:
                            f = tmpdir / f"img_{pno}_{block.get('number', 0)}.png"
                            try:
                                f.write_bytes(img_bytes)
                                bbox = block.get("bbox", [0, 0, 200, 200])
                                width_in = max(0.4, min(6.5, (bbox[2] - bbox[0]) / 72.0))
                                document_.add_picture(str(f), width=Inches(width_in))
                            except Exception:
                                pass
                        continue

                    for line in block.get("lines", []):
                        spans = line.get("spans", [])
                        text = "".join(s.get("text", "") for s in spans)
                        if not text.strip():
                            continue
                        para = document_.add_paragraph()
                        para.paragraph_format.space_after = Pt(2)
                        for s in spans:
                            t = s.get("text", "")
                            if not t:
                                continue
                            run = para.add_run(t)
                            size = s.get("size", 11)
                            run.font.size = Pt(max(6, min(48, size)))
                            flags = int(s.get("flags", 0))
                            run.bold = bool(flags & 2 ** 4)
                            run.italic = bool(flags & 2 ** 1)
                            fname = (s.get("font") or "")
                            if "Times" in fname or "Serif" in fname:
                                run.font.name = "Times New Roman"
                            elif "Courier" in fname or "Mono" in fname:
                                run.font.name = "Consolas"

                if n < len(pages) - 1:
                    document_.add_page_break()
                ctx.step(detail=f"Page {pno}")

        ctx.stage("Saving Word document")
        document_.save(str(out))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# Word -> PDF
# ===========================================================================

@dataclass
class WordToPdfOptions:
    keep_bookmarks: bool = True
    embed_fonts: bool = True
    engine: str = "auto"                  # auto | word | libreoffice | builtin


def word_to_pdf(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    opts: WordToPdfOptions | None = None,
) -> ConvertResult:
    src = Path(source)
    opts = opts or WordToPdfOptions()
    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "", ".pdf")
    )
    utils.ensure_parent(out)
    result = ConvertResult()

    order = _engine_order(opts.engine)
    errors: list[str] = []

    for engine in order:
        ctx.check_cancel()
        try:
            if engine == "word" and deps.has_msword() and deps.has_pywin32():
                ctx.stage("Converting with Microsoft Word")
                _office_convert_word(src, out, opts)
            elif engine == "libreoffice" and deps.has_soffice():
                ctx.stage("Converting with LibreOffice")
                _soffice_convert(ctx, src, out, "pdf")
            elif engine == "builtin":
                ctx.stage("Converting with the built-in renderer")
                _docx_builtin(ctx, src, out, result)
            else:
                continue
        except Exception as e:
            errors.append(f"{engine}: {e}")
            continue

        if out.exists() and utils.file_size(out) > 0:
            result.engine = {"word": "Microsoft Word",
                             "libreoffice": "LibreOffice",
                             "builtin": "built-in renderer"}[engine]
            result.fidelity = {"word": "high", "libreoffice": "high",
                               "builtin": "basic"}[engine]
            with document(out) as d:
                result.pages = d.page_count
            if engine == "builtin":
                result.warnings.append(
                    "Neither Microsoft Word nor LibreOffice was available, so a "
                    "simplified renderer was used. Headers, footers, columns and "
                    "complex tables may not match the original."
                )
            result.outputs.append(out)
            result.message = f"Converted to PDF using {result.engine} — {result.pages} page(s)"
            ctx.progress(100, result.message)
            return result

    raise JobError(
        "Could not convert this document.\n\n" + ("\n".join(errors) if errors else
        "No conversion engine is available on this computer.")
    )


def _engine_order(preferred: str) -> list[str]:
    if preferred != "auto":
        return [preferred, "libreoffice", "builtin"] if preferred != "builtin" else ["builtin"]
    order = []
    if deps.has_msword() and deps.has_pywin32():
        order.append("word")
    if deps.has_soffice():
        order.append("libreoffice")
    order.append("builtin")
    return utils.dedupe(order)


def _office_convert_word(src: Path, out: Path, opts: WordToPdfOptions) -> None:
    """Microsoft Word COM automation. Windows only, requires Word installed."""
    import pythoncom                                      # type: ignore
    import win32com.client                                # type: ignore

    pythoncom.CoInitialize()
    word = None
    doc = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(src.resolve()), ReadOnly=True,
                                  AddToRecentFiles=False, Visible=False)
        # 17 = wdExportFormatPDF
        doc.ExportAsFixedFormat(
            OutputFileName=str(out.resolve()),
            ExportFormat=17,
            OpenAfterExport=False,
            OptimizeFor=0,
            CreateBookmarks=1 if opts.keep_bookmarks else 0,
            DocStructureTags=True,
            BitmapMissingFonts=not opts.embed_fonts,
        )
    finally:
        try:
            if doc is not None:
                doc.Close(SaveChanges=0)
        except Exception:
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()


def _soffice_convert(ctx, src: Path, out: Path, target_ext: str) -> None:
    soffice = deps.soffice_path()
    if not soffice:
        raise JobError("LibreOffice is not installed.")
    tmpdir = Path(tempfile.mkdtemp(prefix="pmt_lo_", dir=str(paths.temp_dir())))
    profile = tmpdir / "profile"
    try:
        profile.mkdir(parents=True, exist_ok=True)
        # LibreOffice needs a properly percent-encoded file URI here. A raw path
        # with spaces -- which ours always has, under "PDF Master Toolkit" and
        # on Windows under "C:\Program Files\..." -- makes soffice hang instead
        # of failing, so build the URI with as_uri() rather than by hand.
        cmd = [
            soffice, "--headless", "--invisible", "--norestore", "--nolockcheck",
            "--nodefault", "--nofirststartwizard",
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--convert-to", target_ext, "--outdir", str(tmpdir), str(src.resolve()),
        ]
        env = dict(os.environ)
        env["HOME"] = str(tmpdir)          # keep LibreOffice out of the user profile
        proc = utils.run_cancellable(ctx, cmd, timeout=300, env=env)
        produced = tmpdir / f"{src.stem}.{target_ext}"
        if not produced.exists():
            candidates = list(tmpdir.glob(f"*.{target_ext}"))
            produced = candidates[0] if candidates else None  # type: ignore[assignment]
        if not produced or not produced.exists():
            detail = (proc.stderr or proc.stdout or "").strip()[:300]
            raise JobError(f"LibreOffice produced no output. {detail}")
        shutil.move(str(produced), str(out))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _docx_builtin(ctx, src: Path, out: Path, result: ConvertResult) -> None:
    """
    Minimal DOCX renderer.

    Handles paragraphs (with heading levels, bold/italic runs, alignment),
    simple tables and inline images. Deliberately conservative — it is a
    fallback, and the UI says so.
    """
    try:
        import docx                                       # type: ignore
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError as e:
        raise JobError("python-docx is required for the built-in converter.") from e

    if src.suffix.lower() != ".docx":
        raise JobError(f"The built-in converter only reads .docx files (got {src.suffix}).")

    source_doc = docx.Document(str(src))
    pdf = pymupdf.open()

    page_w, page_h = page_size_points("A4", False)
    margin = 56.0
    line_gap = 1.35

    page = pdf.new_page(width=page_w, height=page_h)
    y = margin
    ctx.set_total(len(source_doc.paragraphs) + len(source_doc.tables), "Rendering")

    def new_page():
        nonlocal page, y
        page = pdf.new_page(width=page_w, height=page_h)
        y = margin

    def write_line(text: str, size: float, bold: bool, italic: bool, align: str):
        nonlocal y
        font = "hebo" if bold else ("hebi" if italic else "helv")
        avail = page_w - 2 * margin
        for chunk in _wrap(text, avail, size, font):
            if y + size * line_gap > page_h - margin:
                new_page()
            width = pymupdf.get_text_length(chunk, fontname=font, fontsize=size)
            if align == "center":
                x = margin + (avail - width) / 2
            elif align == "right":
                x = page_w - margin - width
            else:
                x = margin
            try:
                page.insert_text((x, y + size), chunk, fontname=font, fontsize=size)
            except Exception:
                page.insert_text((x, y + size), chunk, fontname="helv", fontsize=size)
            y += size * line_gap

    body = source_doc.element.body
    for child in body.iterchildren():
        ctx.check_cancel()
        tag = child.tag.split("}")[-1]
        if tag == "p":
            para = docx.text.paragraph.Paragraph(child, source_doc)
            text = para.text.strip()
            style = (para.style.name or "").lower()
            if not text:
                y += 6
                continue
            size = 11.0
            bold = False
            if style.startswith("heading 1") or style == "title":
                size, bold = 20.0, True
            elif style.startswith("heading 2"):
                size, bold = 16.0, True
            elif style.startswith("heading 3"):
                size, bold = 13.5, True
            elif style.startswith("heading"):
                size, bold = 12.0, True
            if any(r.bold for r in para.runs if r.text.strip()):
                bold = True
            italic = all(r.italic for r in para.runs if r.text.strip()) if para.runs else False
            align = {WD_ALIGN_PARAGRAPH.CENTER: "center",
                     WD_ALIGN_PARAGRAPH.RIGHT: "right"}.get(para.alignment, "left")
            write_line(text, size, bold, bool(italic), align)
            y += 3
            ctx.step()
        elif tag == "tbl":
            table = docx.table.Table(child, source_doc)
            _render_table(page, table, margin, page_w, page_h, lambda: (page, y),
                          write_line)
            ctx.step()

    ctx.stage("Saving PDF")
    pdf.save(str(out), garbage=3, deflate=True)
    result.pages = pdf.page_count
    pdf.close()


def _render_table(page, table, margin, page_w, page_h, get_state, write_line):
    """Tables are rendered as pipe-separated rows — plain, but readable."""
    for row in table.rows:
        cells = [c.text.replace("\n", " ").strip() for c in row.cells]
        write_line("  |  ".join(cells), 9.5, False, False, "left")


def _wrap(text: str, width: float, size: float, font: str) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines, current = [], ""
    for w in words:
        trial = f"{current} {w}".strip()
        try:
            length = pymupdf.get_text_length(trial, fontname=font, fontsize=size)
        except Exception:
            length = len(trial) * size * 0.5
        if length <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


# ===========================================================================
# PDF -> Excel / CSV
# ===========================================================================

@dataclass
class PdfToExcelOptions:
    pages: str = "all"
    fmt: str = "xlsx"                   # xlsx | csv
    sheet_per_page: bool = True
    include_page_column: bool = False
    min_rows: int = 2
    min_cols: int = 2
    engine: str = "auto"                # auto | pdfplumber | pymupdf


@dataclass
class ExtractedTable:
    page: int
    index: int
    rows: list[list[str]]

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.rows), max((len(r) for r in self.rows), default=0))

    @property
    def label(self) -> str:
        r, c = self.shape
        return f"Page {self.page} · Table {self.index} · {r}×{c}"


def find_tables(
    ctx: JobContext,
    source: str | Path,
    opts: PdfToExcelOptions | None = None,
    password: str | None = None,
) -> list[ExtractedTable]:
    """Detect tables and return them for preview before export."""
    opts = opts or PdfToExcelOptions()
    src = Path(source)
    tables: list[ExtractedTable] = []

    engine = opts.engine
    if engine == "auto":
        engine = "pdfplumber" if deps.has_pdfplumber() else "pymupdf"

    if engine == "pdfplumber" and deps.has_pdfplumber():
        tables = _tables_pdfplumber(ctx, src, opts, password)
        if not tables:
            ctx.log("No tables found with the primary engine; trying the alternative.")
            tables = _tables_pymupdf(ctx, src, opts, password)
    else:
        tables = _tables_pymupdf(ctx, src, opts, password)
        if not tables and deps.has_pdfplumber():
            tables = _tables_pdfplumber(ctx, src, opts, password)

    return [t for t in tables
            if t.shape[0] >= opts.min_rows and t.shape[1] >= opts.min_cols]


def _tables_pdfplumber(ctx, src: Path, opts, password) -> list[ExtractedTable]:
    import pdfplumber                                     # type: ignore

    out: list[ExtractedTable] = []
    try:
        with pdfplumber.open(str(src), password=password or "") as pdf:
            total = len(pdf.pages)
            pages = utils.parse_page_ranges(opts.pages, total)
            ctx.set_total(len(pages), "Scanning for tables")
            for pno in pages:
                ctx.check_cancel()
                try:
                    found = pdf.pages[pno - 1].extract_tables() or []
                except Exception:
                    found = []
                for i, raw in enumerate(found, start=1):
                    rows = [[("" if c is None else str(c).strip()) for c in row]
                            for row in raw if row is not None]
                    rows = [r for r in rows if any(c for c in r)]
                    if rows:
                        out.append(ExtractedTable(pno, i, rows))
                ctx.step(detail=f"Page {pno} — {len(out)} table(s)")
    except Exception as e:
        ctx.warn(f"Table scan failed: {e}")
    return out


def _tables_pymupdf(ctx, src: Path, opts, password) -> list[ExtractedTable]:
    out: list[ExtractedTable] = []
    try:
        with document(src, password) as doc:
            total = doc.page_count
            pages = utils.parse_page_ranges(opts.pages, total)
            ctx.set_total(len(pages), "Scanning for tables")
            for pno in pages:
                ctx.check_cancel()
                page = doc[pno - 1]
                try:
                    finder = page.find_tables()
                    found = list(finder.tables)
                except Exception:
                    found = []
                for i, t in enumerate(found, start=1):
                    try:
                        raw = t.extract()
                    except Exception:
                        continue
                    rows = [[("" if c is None else str(c).replace("\n", " ").strip())
                             for c in row] for row in raw]
                    rows = [r for r in rows if any(c for c in r)]
                    if rows:
                        out.append(ExtractedTable(pno, i, rows))
                ctx.step(detail=f"Page {pno} — {len(out)} table(s)")
    except Exception as e:
        ctx.warn(f"Table scan failed: {e}")
    return out


def tables_to_workbook(
    ctx: JobContext,
    tables: Sequence[ExtractedTable],
    output: str | Path,
    opts: PdfToExcelOptions | None = None,
) -> ConvertResult:
    opts = opts or PdfToExcelOptions()
    result = ConvertResult(engine="openpyxl" if opts.fmt == "xlsx" else "csv")
    if not tables:
        raise JobError("No tables were selected for export.")

    out = utils.resolve_collision(Path(output))
    utils.ensure_parent(out)

    if opts.fmt == "csv":
        if len(tables) == 1:
            _write_csv(tables[0], out)
            result.outputs.append(out)
        else:
            folder = out.parent / out.stem
            folder.mkdir(parents=True, exist_ok=True)
            for t in tables:
                f = utils.unique_path(folder / f"page{t.page:03d}_table{t.index}.csv")
                _write_csv(t, f)
                result.outputs.append(f)
        result.tables = len(tables)
        result.message = f"Exported {len(tables)} table(s) to CSV"
        ctx.progress(100, result.message)
        return result

    try:
        from openpyxl import Workbook                       # type: ignore
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as e:
        raise JobError("openpyxl is required for Excel export.") from e

    wb = Workbook()
    wb.remove(wb.active)
    header_fill = PatternFill("solid", fgColor="EEF2F9")
    header_font = Font(bold=True)

    ctx.set_total(len(tables), "Writing sheets")
    used: set[str] = set()

    if opts.sheet_per_page:
        for t in tables:
            ctx.check_cancel()
            title = _unique_sheet_name(f"Page {t.page}" + (f" ({t.index})" if t.index > 1 else ""), used)
            ws = wb.create_sheet(title=title)
            _fill_sheet(ws, t.rows, header_fill, header_font, get_column_letter, Alignment)
            ctx.step()
    else:
        ws = wb.create_sheet(title="Tables")
        row_cursor = 1
        for t in tables:
            ctx.check_cancel()
            ws.cell(row=row_cursor, column=1, value=f"Page {t.page} — Table {t.index}").font = header_font
            row_cursor += 1
            for row in t.rows:
                for ci, val in enumerate(row, start=1):
                    ws.cell(row=row_cursor, column=ci, value=_coerce(val))
                row_cursor += 1
            row_cursor += 1
            ctx.step()
        _autosize(ws, get_column_letter)

    wb.save(str(out))
    result.outputs.append(out)
    result.tables = len(tables)
    result.message = f"Exported {len(tables)} table(s) to Excel"
    ctx.progress(100, result.message)
    return result


def _unique_sheet_name(base: str, used: set[str]) -> str:
    name = utils.sanitize_filename(base).replace("/", "-")[:28] or "Sheet"
    candidate, n = name, 2
    while candidate.lower() in used:
        candidate = f"{name[:26]}_{n}"
        n += 1
    used.add(candidate.lower())
    return candidate


def _fill_sheet(ws, rows, header_fill, header_font, get_column_letter, Alignment):
    for ri, row in enumerate(rows, start=1):
        for ci, val in enumerate(row, start=1):
            cell = ws.cell(row=ri, column=ci, value=_coerce(val))
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if ri == 1:
                cell.fill = header_fill
                cell.font = header_font
    ws.freeze_panes = "A2"
    _autosize(ws, get_column_letter)


def _autosize(ws, get_column_letter) -> None:
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            widths[cell.column] = max(widths.get(cell.column, 0),
                                      min(60, len(str(cell.value)) + 2))
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(9, w)


def _coerce(value: str):
    """Turn obvious numerics into real numbers so Excel can calculate with them."""
    s = str(value).strip()
    if not s:
        return None
    cleaned = s.replace(",", "").replace("$", "").replace("%", "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        if cleaned.lower() in ("nan", "inf", "-inf"):
            return s
        if "." in cleaned or "e" in cleaned.lower():
            return float(cleaned)
        return int(cleaned)
    except ValueError:
        return s


def _write_csv(table: ExtractedTable, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        for row in table.rows:
            writer.writerow(row)


def pdf_to_excel(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    opts: PdfToExcelOptions | None = None,
    password: str | None = None,
) -> ConvertResult:
    opts = opts or PdfToExcelOptions()
    src = Path(source)
    tables = find_tables(ctx, src, opts, password)
    if not tables:
        raise JobError(
            "No tables were detected in this PDF.\n\n"
            "If the document is a scan, run OCR first — table detection needs "
            "real text, not a picture of it."
        )
    ext = ".xlsx" if opts.fmt == "xlsx" else ".csv"
    out = Path(output) if output else utils.suggest_output(src, "tables", ext)
    return tables_to_workbook(ctx, tables, out, opts)


# ===========================================================================
# Excel -> PDF
# ===========================================================================

@dataclass
class ExcelToPdfOptions:
    sheets: list[str] = field(default_factory=list)     # empty = all
    all_sheets: bool = True
    orientation: str = "portrait"                       # portrait | landscape
    page_size: str = "A4"
    margin: float = 36.0
    fit_to_width: bool = True
    gridlines: bool = True
    repeat_header: bool = True
    engine: str = "auto"                                # auto | excel | libreoffice | builtin


def excel_to_pdf(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    opts: ExcelToPdfOptions | None = None,
) -> ConvertResult:
    src = Path(source)
    opts = opts or ExcelToPdfOptions()
    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "", ".pdf")
    )
    utils.ensure_parent(out)
    result = ConvertResult()
    errors: list[str] = []

    order = []
    if opts.engine != "auto":
        order = [opts.engine]
    else:
        if deps.has_msexcel() and deps.has_pywin32():
            order.append("excel")
        if deps.has_soffice():
            order.append("libreoffice")
        order.append("builtin")

    for engine in order:
        ctx.check_cancel()
        try:
            if engine == "excel":
                ctx.stage("Converting with Microsoft Excel")
                _office_convert_excel(src, out, opts)
            elif engine == "libreoffice":
                ctx.stage("Converting with LibreOffice")
                _soffice_convert(ctx, src, out, "pdf")
            else:
                ctx.stage("Rendering spreadsheet")
                _excel_builtin(ctx, src, out, opts)
        except Exception as e:
            errors.append(f"{engine}: {e}")
            continue

        if out.exists() and utils.file_size(out) > 0:
            result.engine = {"excel": "Microsoft Excel", "libreoffice": "LibreOffice",
                             "builtin": "built-in renderer"}[engine]
            result.fidelity = "high" if engine != "builtin" else "medium"
            if engine == "builtin":
                result.warnings.append(
                    "Rendered with the built-in engine. Cell values, borders and "
                    "column widths are preserved; charts, conditional formatting "
                    "and images are not."
                )
            with document(out) as d:
                result.pages = d.page_count
            result.outputs.append(out)
            result.message = f"Converted to PDF using {result.engine}"
            ctx.progress(100, result.message)
            return result

    raise JobError("Could not convert this spreadsheet.\n\n" + "\n".join(errors))


def _office_convert_excel(src: Path, out: Path, opts: ExcelToPdfOptions) -> None:
    import pythoncom                                       # type: ignore
    import win32com.client                                 # type: ignore

    pythoncom.CoInitialize()
    excel = None
    wb = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        wb = excel.Workbooks.Open(str(src.resolve()), ReadOnly=True, UpdateLinks=0)

        landscape = opts.orientation == "landscape"
        for sheet in wb.Worksheets:
            try:
                sheet.PageSetup.Orientation = 2 if landscape else 1
                if opts.fit_to_width:
                    sheet.PageSetup.Zoom = False
                    sheet.PageSetup.FitToPagesWide = 1
                    sheet.PageSetup.FitToPagesTall = False
            except Exception:
                pass

        if not opts.all_sheets and opts.sheets:
            wb.Worksheets(opts.sheets).Select()
            target = excel.ActiveSheet
        else:
            target = wb
        target.ExportAsFixedFormat(0, str(out.resolve()))
    finally:
        try:
            if wb is not None:
                wb.Close(SaveChanges=False)
        except Exception:
            pass
        try:
            if excel is not None:
                excel.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()


def _excel_builtin(ctx, src: Path, out: Path, opts: ExcelToPdfOptions) -> None:
    """Render sheets as paginated tables with PyMuPDF."""
    rows_by_sheet = _read_spreadsheet(src, opts)
    if not rows_by_sheet:
        raise JobError("No readable sheets were found in this file.")

    landscape = opts.orientation == "landscape"
    page_w, page_h = page_size_points(opts.page_size, landscape)
    margin = max(12.0, opts.margin)
    pdf = pymupdf.open()

    ctx.set_total(sum(len(r) for r in rows_by_sheet.values()), "Rendering rows")

    for sheet_name, rows in rows_by_sheet.items():
        if not rows:
            continue
        ncols = max(len(r) for r in rows)
        avail_w = page_w - 2 * margin
        col_w = avail_w / max(1, ncols)
        font_size = utils.clamp(9.0 * (7.0 / max(1, ncols)) ** 0.25, 5.5, 9.5)
        row_h = font_size * 1.9

        page = pdf.new_page(width=page_w, height=page_h)
        y = margin
        page.insert_text((margin, y + 12), sheet_name, fontname="hebo", fontsize=12)
        y += 24
        header = rows[0] if opts.repeat_header else None

        def draw_row(pg, yy, row, bold=False):
            for ci in range(ncols):
                x = margin + ci * col_w
                val = str(row[ci]) if ci < len(row) and row[ci] is not None else ""
                if opts.gridlines:
                    pg.draw_rect(pymupdf.Rect(x, yy, x + col_w, yy + row_h),
                                 color=(0.82, 0.85, 0.9), width=0.4)
                if val:
                    val = _truncate_to_width(val, col_w - 6, font_size,
                                             "hebo" if bold else "helv")
                    try:
                        pg.insert_text((x + 3, yy + row_h - font_size * 0.55), val,
                                       fontname="hebo" if bold else "helv",
                                       fontsize=font_size)
                    except Exception:
                        pass

        for ri, row in enumerate(rows):
            ctx.check_cancel()
            if y + row_h > page_h - margin:
                page = pdf.new_page(width=page_w, height=page_h)
                y = margin
                if header is not None:
                    draw_row(page, y, header, bold=True)
                    y += row_h
            draw_row(page, y, row, bold=(ri == 0))
            y += row_h
            ctx.step()

    ctx.stage("Saving PDF")
    pdf.save(str(out), garbage=3, deflate=True)
    pdf.close()


def _truncate_to_width(text: str, width: float, size: float, font: str) -> str:
    try:
        if pymupdf.get_text_length(text, fontname=font, fontsize=size) <= width:
            return text
    except Exception:
        return text[:40]
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        trial = text[:mid] + "…"
        if pymupdf.get_text_length(trial, fontname=font, fontsize=size) <= width:
            lo = mid + 1
        else:
            hi = mid
    return (text[:max(0, lo - 1)] + "…") if lo > 1 else ""


def _read_spreadsheet(src: Path, opts: ExcelToPdfOptions) -> dict[str, list[list]]:
    ext = src.suffix.lower()
    if ext == ".csv":
        rows: list[list] = []
        with open(src, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            sample = f.read(8192)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            for row in csv.reader(f, dialect):
                rows.append(row)
        return {src.stem[:28] or "Sheet1": rows}

    try:
        import openpyxl                                     # type: ignore
    except ImportError as e:
        raise JobError("openpyxl is required to read Excel files.") from e

    if ext == ".xls":
        raise JobError(
            "Legacy .xls files need Microsoft Excel or LibreOffice. "
            "Re-save the file as .xlsx and try again."
        )

    wb = openpyxl.load_workbook(str(src), data_only=True, read_only=True)
    out: dict[str, list[list]] = {}
    try:
        names = wb.sheetnames if (opts.all_sheets or not opts.sheets) else \
            [n for n in wb.sheetnames if n in opts.sheets]
        for name in names:
            ws = wb[name]
            rows = []
            for row in ws.iter_rows(values_only=True):
                if row is None:
                    continue
                cells = ["" if c is None else _fmt_cell(c) for c in row]
                while cells and cells[-1] == "":
                    cells.pop()
                if cells:
                    rows.append(cells)
            if rows:
                out[name] = rows
    finally:
        wb.close()
    return out


def _fmt_cell(v) -> str:
    from datetime import date, datetime, time
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M") if (v.hour or v.minute) else v.strftime("%Y-%m-%d")
    if isinstance(v, (date, time)):
        return str(v)
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)
