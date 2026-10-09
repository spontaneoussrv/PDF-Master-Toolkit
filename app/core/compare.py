"""
Document comparison.

Text mode runs a word-level diff per page and classifies every change as
added / removed / changed. Visual mode renders both pages and reports the
proportion of pixels that differ, with a highlighted difference image.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from app.core import utils
from app.core.jobs import JobContext, JobError
from app.core.pdfbase import document, pymupdf

try:
    from PIL import Image, ImageChops, ImageDraw
except ImportError:                                # pragma: no cover
    Image = None                                   # type: ignore

try:
    import numpy as np
except ImportError:                                # pragma: no cover
    np = None                                      # type: ignore


CHANGE_KINDS = ("added", "removed", "changed", "identical")


@dataclass
class TextChange:
    kind: str                       # added | removed | changed
    page_a: int | None
    page_b: int | None
    text_a: str = ""
    text_b: str = ""

    @property
    def label(self) -> str:
        return {"added": "Added", "removed": "Removed", "changed": "Changed"}[self.kind]


@dataclass
class PageComparison:
    index: int
    page_a: int | None
    page_b: int | None
    status: str = "identical"            # identical | changed | added | removed
    similarity: float = 1.0
    pixel_diff: float = 0.0
    changes: list[TextChange] = field(default_factory=list)
    diff_image: Path | None = None


@dataclass
class CompareResult:
    pages: list[PageComparison] = field(default_factory=list)
    pages_a: int = 0
    pages_b: int = 0
    changed_pages: int = 0
    added_pages: int = 0
    removed_pages: int = 0
    identical_pages: int = 0
    total_changes: int = 0
    similarity: float = 1.0
    mode: str = "text"
    message: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return self.total_changes == 0 and self.added_pages == 0 and self.removed_pages == 0


@dataclass
class CompareOptions:
    mode: str = "text"                  # text | visual | both
    ignore_whitespace: bool = True
    ignore_case: bool = False
    ignore_numbers: bool = False
    pixel_threshold: int = 28           # 0..255 per-channel difference to count
    render_dpi: int = 96
    write_diff_images: bool = False
    diff_dir: Path | None = None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def compare(
    ctx: JobContext,
    file_a: str | Path,
    file_b: str | Path,
    opts: CompareOptions | None = None,
    password_a: str | None = None,
    password_b: str | None = None,
) -> CompareResult:
    opts = opts or CompareOptions()
    a_path, b_path = Path(file_a), Path(file_b)
    result = CompareResult(mode=opts.mode)

    with document(a_path, password_a) as da, document(b_path, password_b) as db:
        result.pages_a = da.page_count
        result.pages_b = db.page_count
        n = max(da.page_count, db.page_count)
        if n == 0:
            raise JobError("Both documents are empty.")

        ctx.set_total(n, "Comparing pages")
        for i in range(n):
            ctx.check_cancel()
            has_a = i < da.page_count
            has_b = i < db.page_count
            pc = PageComparison(index=i + 1,
                                page_a=(i + 1) if has_a else None,
                                page_b=(i + 1) if has_b else None)

            if not has_b:
                pc.status = "removed"
                pc.similarity = 0.0
                result.removed_pages += 1
                text_a = _page_text(da, i)
                if text_a.strip():
                    pc.changes.append(TextChange("removed", i + 1, None, text_a[:4000]))
            elif not has_a:
                pc.status = "added"
                pc.similarity = 0.0
                result.added_pages += 1
                text_b = _page_text(db, i)
                if text_b.strip():
                    pc.changes.append(TextChange("added", None, i + 1, "", text_b[:4000]))
            else:
                if opts.mode in ("text", "both"):
                    ta = _normalize(_page_text(da, i), opts)
                    tb = _normalize(_page_text(db, i), opts)
                    pc.changes = _diff_words(ta, tb, i + 1)
                    pc.similarity = difflib.SequenceMatcher(None, ta, tb).ratio()
                if opts.mode in ("visual", "both"):
                    pc.pixel_diff, pc.diff_image = _visual_diff(
                        da, db, i, opts, a_path.stem)

                changed = bool(pc.changes) or (
                    opts.mode in ("visual", "both") and pc.pixel_diff > 0.001)
                pc.status = "changed" if changed else "identical"
                if changed:
                    result.changed_pages += 1
                else:
                    result.identical_pages += 1

            result.total_changes += len(pc.changes)
            result.pages.append(pc)
            ctx.step(detail=f"Page {i + 1}")

    sims = [p.similarity for p in result.pages] or [1.0]
    result.similarity = sum(sims) / len(sims)

    if result.identical:
        result.message = "The two documents are identical."
    else:
        bits = []
        if result.changed_pages:
            bits.append(f"{result.changed_pages} page(s) changed")
        if result.added_pages:
            bits.append(f"{result.added_pages} added")
        if result.removed_pages:
            bits.append(f"{result.removed_pages} removed")
        result.message = " · ".join(bits) + f" · {result.similarity * 100:.1f}% similar"

    ctx.progress(100, result.message)
    return result


def _page_text(doc, index: int) -> str:
    try:
        return doc[index].get_text("text", sort=True) or ""
    except Exception:
        return ""


def _normalize(text: str, opts: CompareOptions) -> str:
    if opts.ignore_case:
        text = text.lower()
    if opts.ignore_numbers:
        import re
        text = re.sub(r"\d+", "#", text)
    if opts.ignore_whitespace:
        text = " ".join(text.split())
    return text


def _diff_words(a: str, b: str, page: int) -> list[TextChange]:
    wa, wb = a.split(), b.split()
    changes: list[TextChange] = []
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        seg_a = " ".join(wa[i1:i2])
        seg_b = " ".join(wb[j1:j2])
        if tag == "replace":
            changes.append(TextChange("changed", page, page, seg_a, seg_b))
        elif tag == "delete":
            changes.append(TextChange("removed", page, page, seg_a, ""))
        elif tag == "insert":
            changes.append(TextChange("added", page, page, "", seg_b))
    return changes


# ---------------------------------------------------------------------------
# visual
# ---------------------------------------------------------------------------

def _visual_diff(da, db, index: int, opts: CompareOptions,
                 stem: str) -> tuple[float, Path | None]:
    if Image is None:
        return 0.0, None
    try:
        zoom = max(24, opts.render_dpi) / 72.0
        m = pymupdf.Matrix(zoom, zoom)
        pa = da[index].get_pixmap(matrix=m, alpha=False)
        pb = db[index].get_pixmap(matrix=m, alpha=False)
        ia = Image.frombytes("RGB", (pa.width, pa.height), pa.samples)
        ib = Image.frombytes("RGB", (pb.width, pb.height), pb.samples)
    except Exception:
        return 0.0, None

    if ia.size != ib.size:
        size = (max(ia.width, ib.width), max(ia.height, ib.height))
        canvas_a = Image.new("RGB", size, "white")
        canvas_b = Image.new("RGB", size, "white")
        canvas_a.paste(ia, (0, 0))
        canvas_b.paste(ib, (0, 0))
        ia, ib = canvas_a, canvas_b

    diff = ImageChops.difference(ia, ib).convert("L")

    if np is not None:
        arr = np.asarray(diff)
        mask = arr > opts.pixel_threshold
        ratio = float(mask.mean())
    else:
        hist = diff.histogram()
        above = sum(hist[opts.pixel_threshold:])
        ratio = above / max(1, ia.width * ia.height)

    out_path = None
    if opts.write_diff_images and opts.diff_dir and ratio > 0.0005:
        try:
            folder = Path(opts.diff_dir)
            folder.mkdir(parents=True, exist_ok=True)
            overlay = ib.convert("RGB").copy()
            red = Image.new("RGB", overlay.size, (255, 60, 60))
            mask_img = diff.point(lambda p: 255 if p > opts.pixel_threshold else 0)
            overlay.paste(red, (0, 0), mask_img)
            out_path = utils.unique_path(folder / f"{stem}_diff_p{index + 1:03d}.png")
            overlay.save(out_path, "PNG")
        except Exception:
            out_path = None

    return ratio, out_path


# ---------------------------------------------------------------------------
# report export
# ---------------------------------------------------------------------------

def export_report(
    ctx: JobContext,
    result: CompareResult,
    output: str | Path,
    file_a: str | Path,
    file_b: str | Path,
    fmt: str = "pdf",
) -> Path:
    out = utils.resolve_collision(Path(output))
    utils.ensure_parent(out)

    if fmt == "txt":
        out.write_text(_text_report(result, file_a, file_b), encoding="utf-8")
    elif fmt == "html":
        out.write_text(_html_report(result, file_a, file_b), encoding="utf-8")
    elif fmt == "csv":
        import csv as _csv
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.writer(f)
            w.writerow(["Page", "Status", "Similarity %", "Change", "Document A", "Document B"])
            for p in result.pages:
                if not p.changes:
                    w.writerow([p.index, p.status, f"{p.similarity * 100:.1f}", "", "", ""])
                for c in p.changes:
                    w.writerow([p.index, p.status, f"{p.similarity * 100:.1f}",
                                c.label, c.text_a, c.text_b])
    else:
        _pdf_report(out, result, file_a, file_b)

    ctx.progress(100, f"Report saved as {out.name}")
    return out


def _text_report(result: CompareResult, a, b) -> str:
    lines = [
        "PDF COMPARISON REPORT",
        "=" * 60,
        f"Document A : {Path(a).name}  ({result.pages_a} pages)",
        f"Document B : {Path(b).name}  ({result.pages_b} pages)",
        f"Similarity : {result.similarity * 100:.1f}%",
        f"Summary    : {result.message}",
        "",
    ]
    for p in result.pages:
        if p.status == "identical":
            continue
        lines.append(f"--- Page {p.index} [{p.status}] "
                     f"similarity {p.similarity * 100:.1f}% ---")
        for c in p.changes[:200]:
            if c.kind == "added":
                lines.append(f"  + {c.text_b}")
            elif c.kind == "removed":
                lines.append(f"  - {c.text_a}")
            else:
                lines.append(f"  ~ {c.text_a}")
                lines.append(f"    → {c.text_b}")
        lines.append("")
    return "\n".join(lines)


def _html_report(result: CompareResult, a, b) -> str:
    import html as _html
    rows = []
    for p in result.pages:
        if p.status == "identical":
            continue
        for c in p.changes[:400]:
            cls = {"added": "add", "removed": "del", "changed": "chg"}[c.kind]
            rows.append(
                f"<tr class='{cls}'><td>{p.index}</td><td>{c.label}</td>"
                f"<td>{_html.escape(c.text_a[:400])}</td>"
                f"<td>{_html.escape(c.text_b[:400])}</td></tr>"
            )
    return f"""<!doctype html><meta charset="utf-8">
<title>PDF Comparison Report</title>
<style>
 body{{font-family:'Segoe UI',system-ui,sans-serif;margin:32px;color:#141B2D;background:#F7F9FC}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#5A6684;font-size:13px;margin-bottom:20px}}
 table{{border-collapse:collapse;width:100%;background:#fff;border-radius:10px;overflow:hidden;
        box-shadow:0 1px 3px rgba(16,24,40,.08);font-size:13px}}
 th{{background:#F0F3F9;text-align:left;padding:10px;font-size:12px;letter-spacing:.3px}}
 td{{padding:9px 10px;border-top:1px solid #EDF0F6;vertical-align:top}}
 tr.add td:nth-child(4){{background:#E9F8F0;color:#0A6C42}}
 tr.del td:nth-child(3){{background:#FDECEA;color:#96271B}}
 tr.chg td:nth-child(3),tr.chg td:nth-child(4){{background:#FFF6E6}}
</style>
<h1>PDF Comparison Report</h1>
<div class="sub">{_html.escape(Path(a).name)} ({result.pages_a} pages) &nbsp;vs&nbsp;
{_html.escape(Path(b).name)} ({result.pages_b} pages) — {result.similarity * 100:.1f}% similar</div>
<table><tr><th>Page</th><th>Change</th><th>Document A</th><th>Document B</th></tr>
{''.join(rows) or '<tr><td colspan=4>No differences found.</td></tr>'}</table>"""


def _pdf_report(out: Path, result: CompareResult, a, b) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 56.0
    margin = 48.0
    width = 595 - 2 * margin

    def line(text, size=10.0, bold=False, color=(0.08, 0.11, 0.18), gap=1.35):
        nonlocal page, y
        font = "hebo" if bold else "helv"
        for chunk in _wrap(text, width, size, font):
            if y > 800:
                page = doc.new_page(width=595, height=842)
                y = 56.0
            page.insert_text((margin, y), chunk, fontname=font, fontsize=size, color=color)
            y += size * gap

    line("PDF Comparison Report", 18, True)
    y += 6
    line(f"Document A: {Path(a).name}  ({result.pages_a} pages)", 10, color=(0.35, 0.4, 0.5))
    line(f"Document B: {Path(b).name}  ({result.pages_b} pages)", 10, color=(0.35, 0.4, 0.5))
    line(f"Overall similarity: {result.similarity * 100:.1f}%", 11, True)
    line(result.message, 10, color=(0.35, 0.4, 0.5))
    y += 12

    for p in result.pages:
        if p.status == "identical":
            continue
        line(f"Page {p.index} — {p.status} ({p.similarity * 100:.1f}% similar)", 11, True)
        for c in p.changes[:60]:
            if c.kind == "added":
                line(f"+  {c.text_b}", 9, color=(0.04, 0.42, 0.26))
            elif c.kind == "removed":
                line(f"−  {c.text_a}", 9, color=(0.59, 0.15, 0.11))
            else:
                line(f"~  {c.text_a}", 9, color=(0.55, 0.38, 0.02))
                line(f"   → {c.text_b}", 9, color=(0.04, 0.42, 0.26))
        if len(p.changes) > 60:
            line(f"   … {len(p.changes) - 60} more change(s) on this page", 9,
                 color=(0.5, 0.54, 0.62))
        y += 8

    doc.save(str(out), garbage=3, deflate=True)
    doc.close()


def _wrap(text: str, width: float, size: float, font: str) -> list[str]:
    words = str(text).split()
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
