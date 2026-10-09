"""Text extraction and export (TXT / DOCX / JSON / CSV / clipboard)."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.core import utils
from app.core.jobs import JobContext, JobError
from app.core.pdfbase import document, document_text_ratio, page_has_text

SCANNED_HINT = "This document appears to be scanned. Run OCR?"


@dataclass
class TextExtractOptions:
    pages: str = "all"
    layout: str = "text"                 # text | blocks | words | raw | html
    keep_page_breaks: bool = True
    page_marker: str = "\n\n──────── Page {page} ────────\n\n"
    strip_hyphens: bool = True
    collapse_whitespace: bool = False
    dehyphenate_lines: bool = True
    sort_by_position: bool = True


@dataclass
class TextResult:
    text: str = ""
    pages: list[str] = field(default_factory=list)
    page_count: int = 0
    empty_pages: list[int] = field(default_factory=list)
    looks_scanned: bool = False
    text_ratio: float = 0.0
    words: int = 0
    characters: int = 0
    outputs: list[Path] = field(default_factory=list)
    message: str = ""


def extract_text(
    ctx: JobContext,
    source: str | Path,
    opts: TextExtractOptions | None = None,
    password: str | None = None,
) -> TextResult:
    opts = opts or TextExtractOptions()
    src = Path(source)
    result = TextResult()

    with document(src, password) as doc:
        total = doc.page_count
        result.page_count = total
        result.text_ratio = document_text_ratio(doc)
        result.looks_scanned = result.text_ratio < 0.15

        pages = utils.parse_page_ranges(opts.pages, total)
        if not pages:
            raise JobError("The selected page range contains no pages.")

        ctx.set_total(len(pages), "Extracting text")
        chunks: list[str] = []

        for pno in pages:
            ctx.check_cancel()
            page = doc[pno - 1]
            try:
                if opts.layout == "blocks":
                    raw = _blocks_text(page, opts.sort_by_position)
                elif opts.layout == "words":
                    raw = _words_text(page)
                elif opts.layout == "html":
                    raw = page.get_text("html") or ""
                elif opts.layout == "raw":
                    raw = page.get_text("rawtext") if hasattr(page, "get_text") else ""
                    raw = raw or page.get_text("text") or ""
                else:
                    sort = opts.sort_by_position
                    raw = page.get_text("text", sort=sort) or ""
            except Exception as e:
                ctx.warn(f"Page {pno}: {e}")
                raw = ""

            cleaned = _clean(raw, opts)
            result.pages.append(cleaned)
            if not cleaned.strip():
                result.empty_pages.append(pno)
            if opts.keep_page_breaks and len(pages) > 1:
                chunks.append(opts.page_marker.format(page=pno, total=total) + cleaned)
            else:
                chunks.append(cleaned)
            ctx.step(detail=f"Page {pno}")

        result.text = "\n".join(chunks).strip()

    result.characters = len(result.text)
    result.words = len(result.text.split())
    if result.looks_scanned and result.characters < 40:
        result.message = SCANNED_HINT
    else:
        result.message = f"Extracted {result.words:,} words from {len(result.pages)} page(s)"
    ctx.progress(100, result.message)
    return result


def _blocks_text(page, sort: bool) -> str:
    blocks = page.get_text("blocks") or []
    if sort:
        blocks = sorted(blocks, key=lambda b: (round(b[1], 1), round(b[0], 1)))
    return "\n\n".join((b[4] or "").strip() for b in blocks if len(b) > 4 and (b[4] or "").strip())


def _words_text(page) -> str:
    words = page.get_text("words") or []
    lines: dict[tuple, list] = {}
    for w in words:
        key = (w[5], w[6]) if len(w) > 6 else (0, round(w[1], 1))
        lines.setdefault(key, []).append(w)
    out = []
    for key in sorted(lines, key=lambda k: (min(w[1] for w in lines[k]),)):
        ws = sorted(lines[key], key=lambda w: w[0])
        out.append(" ".join(w[4] for w in ws))
    return "\n".join(out)


def _clean(text: str, opts: TextExtractOptions) -> str:
    if not text:
        return ""
    if opts.dehyphenate_lines:
        text = text.replace("-\n", "")
    if opts.strip_hyphens:
        text = text.replace("­", "")
    if opts.collapse_whitespace:
        text = " ".join(text.split())
    else:
        text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip()


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

EXPORT_FORMATS = {
    "txt": "Plain text (.txt)",
    "docx": "Word document (.docx)",
    "json": "Structured JSON (.json)",
    "csv": "CSV, one row per page (.csv)",
    "md": "Markdown (.md)",
}


def export_text(
    ctx: JobContext,
    result: TextResult,
    output: str | Path,
    fmt: str = "txt",
    source_name: str = "",
) -> Path:
    out = utils.resolve_collision(Path(output))
    utils.ensure_parent(out)
    ctx.stage(f"Writing {fmt.upper()}")

    if fmt == "txt":
        out.write_text(result.text, encoding="utf-8")
    elif fmt == "md":
        header = f"# {source_name or out.stem}\n\n" if source_name else ""
        out.write_text(header + result.text, encoding="utf-8")
    elif fmt == "json":
        payload = {
            "source": source_name,
            "page_count": result.page_count,
            "words": result.words,
            "characters": result.characters,
            "looks_scanned": result.looks_scanned,
            "pages": [{"page": i + 1, "text": t} for i, t in enumerate(result.pages)],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    elif fmt == "csv":
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Page", "Characters", "Words", "Text"])
            for i, t in enumerate(result.pages, start=1):
                w.writerow([i, len(t), len(t.split()), t])
    elif fmt == "docx":
        _write_docx(out, result, source_name)
    else:
        raise JobError(f"Unsupported export format: {fmt}")

    ctx.progress(100, f"Saved {out.name}")
    return out


def _write_docx(out: Path, result: TextResult, source_name: str) -> None:
    try:
        import docx                                        # type: ignore
        from docx.shared import Pt
    except ImportError as e:
        raise JobError("python-docx is required to export Word documents.") from e

    d = docx.Document()
    if source_name:
        h = d.add_heading(Path(source_name).stem, level=1)
    style = d.styles["Normal"]
    style.font.size = Pt(11)

    for i, page_text in enumerate(result.pages, start=1):
        if len(result.pages) > 1:
            d.add_heading(f"Page {i}", level=3)
        for para in (page_text or "").split("\n\n"):
            para = para.strip()
            if para:
                d.add_paragraph(para)
        if i < len(result.pages):
            d.add_page_break()
    d.save(str(out))


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

@dataclass
class SearchHit:
    page: int
    rect: tuple[float, float, float, float]
    snippet: str = ""


def search(
    ctx: JobContext,
    source: str | Path,
    term: str,
    password: str | None = None,
    case_sensitive: bool = False,
    max_hits: int = 5000,
) -> list[SearchHit]:
    from app.core.pdfbase import pymupdf

    term = (term or "").strip()
    if not term:
        return []
    hits: list[SearchHit] = []
    flags = 0 if case_sensitive else getattr(pymupdf, "TEXT_IGNORECASE", 0)

    with document(source, password) as doc:
        ctx.set_total(doc.page_count, "Searching")
        for pno in range(doc.page_count):
            ctx.check_cancel()
            page = doc[pno]
            try:
                found = page.search_for(term, flags=flags) if flags else page.search_for(term)
            except Exception:
                found = []
            for r in found:
                hits.append(SearchHit(pno + 1, (r.x0, r.y0, r.x1, r.y1),
                                      _snippet(page, r, term)))
                if len(hits) >= max_hits:
                    ctx.step()
                    return hits
            ctx.step()
    return hits


def _snippet(page, rect, term: str, width: int = 90) -> str:
    from app.core.pdfbase import pymupdf
    try:
        band = pymupdf.Rect(page.rect.x0, rect.y0 - 2, page.rect.x1, rect.y1 + 2)
        line = (page.get_textbox(band) or "").replace("\n", " ").strip()
    except Exception:
        return term
    if not line:
        return term
    idx = line.lower().find(term.lower())
    if idx < 0:
        return line[:width]
    start = max(0, idx - width // 3)
    return ("…" if start else "") + line[start:start + width].strip() + "…"
