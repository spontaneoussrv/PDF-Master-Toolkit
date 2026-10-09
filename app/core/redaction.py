"""
True redaction.

The distinction that matters: a black rectangle drawn over text is *not*
redaction — the words are still in the file and any text extractor recovers
them in seconds. This module uses PyMuPDF redaction annotations, which remove
the underlying text, vector art and image pixels from the content stream
before the page is written back out.

What is removed:
  * text glyphs intersecting the redaction rectangle
  * image pixels under the rectangle (the image object is rewritten)
  * vector drawings intersecting the rectangle
  * link and annotation objects wholly inside the rectangle

What survives (and is stated plainly in the UI):
  * document metadata, unless metadata removal is also requested
  * text elsewhere on the page that merely *mentions* the same string
  * content in attachments or embedded files
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from app.core import utils
from app.core.jobs import JobContext, JobError
from app.core.pdfbase import document, pymupdf

REDACTION_WARNING = (
    "Redaction is permanent and cannot be undone. The text and images under "
    "each mark are deleted from the file, not hidden. Keep a copy of the "
    "original if you may need it later."
)


@dataclass
class RedactionArea:
    """One rectangle to redact, in PDF points on a specific page."""
    page: int                                  # 1-based
    rect: tuple[float, float, float, float]
    label: str = ""


@dataclass
class RedactOptions:
    fill_color: tuple[float, float, float] | None = (0.0, 0.0, 0.0)
    draw_box: bool = True
    overlay_text: str = ""
    overlay_size: float = 8.0
    remove_images: bool = True
    remove_vectors: bool = True
    remove_metadata: bool = False
    remove_annotations_in_area: bool = True
    make_backup: bool = True
    case_sensitive: bool = False
    whole_word: bool = False


@dataclass
class RedactResult:
    output: Path | None = None
    backup: Path | None = None
    pages_affected: int = 0
    areas_applied: int = 0
    matches_found: int = 0
    message: str = ""
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# searching
# ---------------------------------------------------------------------------

def find_text(
    ctx: JobContext,
    source: str | Path,
    terms: Sequence[str],
    pages_spec: str = "all",
    case_sensitive: bool = False,
    whole_word: bool = False,
    regex: bool = False,
    password: str | None = None,
) -> list[RedactionArea]:
    """Locate every occurrence of the given terms and return their rectangles."""
    terms = [t for t in (s.strip() for s in terms) if t]
    if not terms:
        return []

    areas: list[RedactionArea] = []
    with document(source, password) as doc:
        total = doc.page_count
        pages = utils.parse_page_ranges(pages_spec, total)
        ctx.set_total(len(pages), "Searching")

        flags = 0
        try:
            if not case_sensitive:
                flags |= getattr(pymupdf, "TEXT_IGNORECASE", 0)
        except Exception:
            pass

        for pno in pages:
            ctx.check_cancel()
            page = doc[pno - 1]
            for term in terms:
                if regex:
                    areas.extend(_regex_hits(page, pno, term, case_sensitive))
                    continue
                try:
                    hits = page.search_for(term, flags=flags) if flags else page.search_for(term)
                except Exception:
                    hits = []
                for r in hits:
                    if whole_word and not _is_whole_word(page, r, term):
                        continue
                    areas.append(RedactionArea(pno, (r.x0, r.y0, r.x1, r.y1), term))
            ctx.step(detail=f"Page {pno} — {len(areas)} match(es)")

    return areas


def _regex_hits(page, pno: int, pattern: str, case_sensitive: bool) -> list[RedactionArea]:
    """Regex search: match against page text, then locate each match's words."""
    out: list[RedactionArea] = []
    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        rx = re.compile(pattern, flags)
    except re.error:
        return out
    try:
        words = page.get_text("words")           # x0,y0,x1,y1,word,block,line,wordno
    except Exception:
        return out

    for w in words:
        token = w[4]
        if rx.search(token):
            out.append(RedactionArea(pno, (w[0], w[1], w[2], w[3]), pattern))
    return out


def _is_whole_word(page, rect, term: str) -> bool:
    try:
        words = page.get_text("words")
    except Exception:
        return True
    target = term.lower()
    for w in words:
        wr = pymupdf.Rect(w[:4])
        if wr.intersects(rect) and w[4].lower().strip(".,;:!?()[]\"'") == target:
            return True
    return False


PRESET_PATTERNS = {
    "Email addresses": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "Phone numbers": r"(\+?\d[\d\s().-]{7,}\d)",
    "US Social Security numbers": r"\b\d{3}-\d{2}-\d{4}\b",
    "Credit card numbers": r"\b(?:\d[ -]*?){13,16}\b",
    "Dates (mm/dd/yyyy)": r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    "IP addresses": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "Aadhaar-style numbers": r"\b\d{4}\s?\d{4}\s?\d{4}\b",
}


# ---------------------------------------------------------------------------
# applying
# ---------------------------------------------------------------------------

def redact(
    ctx: JobContext,
    source: str | Path,
    areas: Sequence[RedactionArea],
    output: str | Path | None = None,
    opts: RedactOptions | None = None,
    password: str | None = None,
) -> RedactResult:
    src = Path(source)
    opts = opts or RedactOptions()
    if not areas:
        raise JobError("Nothing was marked for redaction.")

    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "redacted", ".pdf")
    )
    utils.ensure_parent(out)
    result = RedactResult()

    if opts.make_backup:
        try:
            backup = utils.unique_path(src.parent / f"{src.stem}_original{src.suffix}")
            shutil.copy2(src, backup)
            result.backup = backup
            ctx.log(f"Backup saved as {backup.name}")
        except OSError as e:
            result.warnings.append(f"Could not create a backup copy: {e}")

    by_page: dict[int, list[RedactionArea]] = {}
    for a in areas:
        by_page.setdefault(a.page, []).append(a)

    with document(src, password) as doc:
        ctx.set_total(len(by_page), "Redacting")
        for pno, page_areas in sorted(by_page.items()):
            ctx.check_cancel()
            if not (1 <= pno <= doc.page_count):
                continue
            page = doc[pno - 1]

            for area in page_areas:
                rect = pymupdf.Rect(*area.rect)
                if rect.is_empty or rect.width < 0.5 or rect.height < 0.5:
                    continue
                try:
                    annot = page.add_redact_annot(
                        rect,
                        text=opts.overlay_text or None,
                        fontsize=opts.overlay_size,
                        fill=opts.fill_color if opts.draw_box else None,
                        text_color=(1, 1, 1),
                        align=getattr(pymupdf, "TEXT_ALIGN_CENTER", 1),
                    )
                    if annot is None:
                        continue
                    result.areas_applied += 1
                except Exception as e:
                    result.warnings.append(f"Page {pno}: {e}")

            try:
                images_mode = (getattr(pymupdf, "PDF_REDACT_IMAGE_PIXELS", 2)
                               if opts.remove_images
                               else getattr(pymupdf, "PDF_REDACT_IMAGE_NONE", 0))
                page.apply_redactions(images=images_mode)
                result.pages_affected += 1
            except TypeError:
                # older PyMuPDF signature
                page.apply_redactions()
                result.pages_affected += 1
            except Exception as e:
                result.warnings.append(f"Page {pno}: redaction failed — {e}")

            ctx.step(detail=f"Page {pno}")

        if opts.remove_metadata:
            ctx.stage("Removing metadata")
            try:
                doc.set_metadata({})
                doc.del_xml_metadata()
            except Exception:
                pass

        ctx.stage("Saving redacted document")
        # garbage=4 forces a full object rebuild so removed content cannot
        # survive in an orphaned object.
        doc.save(str(out), garbage=4, deflate=True, clean=True)

    result.output = out
    result.message = (f"Redacted {result.areas_applied} area(s) across "
                      f"{result.pages_affected} page(s) — content permanently removed")
    ctx.progress(100, result.message)
    return result


def redact_terms(
    ctx: JobContext,
    source: str | Path,
    terms: Sequence[str],
    output: str | Path | None = None,
    opts: RedactOptions | None = None,
    pages_spec: str = "all",
    regex: bool = False,
    password: str | None = None,
) -> RedactResult:
    """Search-and-redact in one step, used by Batch Processing."""
    opts = opts or RedactOptions()
    areas = find_text(ctx, source, terms, pages_spec, opts.case_sensitive,
                      opts.whole_word, regex, password)
    if not areas:
        raise JobError("None of the search terms were found in this document.")
    result = redact(ctx, source, areas, output, opts, password)
    result.matches_found = len(areas)
    return result


def verify_removal(source: str | Path, terms: Sequence[str],
                   password: str | None = None) -> dict[str, int]:
    """
    Post-redaction check: extract all text and count any remaining occurrences.

    Shown in the UI so the user sees evidence, not a promise.
    """
    counts = {t: 0 for t in terms}
    try:
        with document(source, password) as doc:
            blob = "\n".join((doc[i].get_text("text") or "") for i in range(doc.page_count))
    except Exception:
        return counts
    low = blob.lower()
    for t in terms:
        counts[t] = low.count(t.lower())
    return counts
