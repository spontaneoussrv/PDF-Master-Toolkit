"""
Visit Sorter — sorts scanned visit-record PDFs into date order.

A scanned care-documentation PDF often contains many visit "sets" (RN visits
and Aide visits) in arbitrary order. This module detects each set from the
text on its pages, exports them to Excel for review, rebuilds the PDF in date
order and splits it into size-capped parts:

    detect_visits()        scan the PDF and return the visit blocks
    export_worksheet()     write the blocks to Excel for review/editing
    load_worksheet()       read the (possibly edited) Excel back
    build_sorted_pdf()     rebuild the PDF in date order
    split_by_size()        split into parts under a size cap, never mid-visit

Detection rules are data, not code, so new document layouts can be added
without touching the algorithm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

from app import branding
from app.core import utils
from app.core.jobs import JobContext, JobError
from app.core.pdfbase import document, pymupdf


# ---------------------------------------------------------------------------
# detection rules
# ---------------------------------------------------------------------------

@dataclass
class VisitRule:
    """How to recognise one kind of visit."""
    name: str
    date_pattern: str                  # regex with 3 groups: month, day, year
    end_pattern: str = ""              # regex that closes a set (e.g. a signature line)
    new_date_ends_set: bool = True     # a different date starts a new set
    enabled: bool = True


DEFAULT_RULES: list[VisitRule] = [
    VisitRule(
        name="AIDE",
        date_pattern=r"START\s*TIME.*?(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})",
        end_pattern="",
        new_date_ends_set=True,
    ),
    VisitRule(
        name="RN",
        date_pattern=r"VISIT\s*DATE.*?(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})",
        end_pattern=r"SIGNATURE\s+OF\s+NURSE",
        new_date_ends_set=True,
    ),
]

# Fallback used when no rule matches anywhere in the document.
GENERIC_DATE = r"(?:DATE|DATED|VISIT|SERVICE)\D{0,20}(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})"

DATE_ORDER = "mdy"          # US-style, matching the source documents


@dataclass
class Visit:
    """One detected visit block."""
    date: datetime | None
    kind: str
    pages: list[int] = field(default_factory=list)      # 1-based page numbers
    sets: int = 1
    note: str = ""

    @property
    def date_text(self) -> str:
        return self.date.strftime("%m/%d/%Y") if self.date else ""

    @property
    def pages_text(self) -> str:
        return utils.format_page_ranges(self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass
class DetectionResult:
    visits: list[Visit] = field(default_factory=list)
    total_pages: int = 0
    unassigned: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def assigned_pages(self) -> int:
        return sum(v.page_count for v in self.visits)

    @property
    def message(self) -> str:
        return (f"Found {len(self.visits)} visit(s) covering "
                f"{self.assigned_pages} of {self.total_pages} pages")


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

def _safe_date(y, m, d) -> datetime | None:
    try:
        year = int(y)
        if year < 100:
            year += 2000 if year < 70 else 1900
        return datetime(year, int(m), int(d))
    except (TypeError, ValueError):
        return None


def _match_page(text: str, rules: Sequence[VisitRule]) -> tuple[datetime | None, str]:
    if not text:
        return None, ""
    flat = text.replace("\n", " ").upper()
    for rule in rules:
        if not rule.enabled:
            continue
        try:
            m = re.search(rule.date_pattern, flat, re.I | re.S)
        except re.error:
            continue
        if m and len(m.groups()) >= 3:
            a, b, c = m.group(1), m.group(2), m.group(3)
            dt = _safe_date(c, a, b) if DATE_ORDER == "mdy" else _safe_date(c, b, a)
            if dt:
                return dt, rule.name
    return None, ""


def _closes_set(text: str, kind: str, rules: Sequence[VisitRule]) -> bool:
    flat = (text or "").replace("\n", " ").upper()
    for rule in rules:
        if rule.name == kind and rule.end_pattern:
            try:
                return bool(re.search(rule.end_pattern, flat, re.I))
            except re.error:
                return False
    return False


def detect_visits(
    ctx: JobContext,
    source: str | Path,
    rules: Sequence[VisitRule] | None = None,
    password: str | None = None,
    group_same_date: bool = True,
    use_fallback: bool = True,
) -> DetectionResult:
    """
    Walk the document once, opening a new visit block whenever a rule fires and
    closing it on an end marker or a new date — the same state machine as the
    original script, with the page bookkeeping made explicit.
    """
    rules = list(rules or DEFAULT_RULES)
    result = DetectionResult()
    blocks: list[Visit] = []

    with document(source, password) as doc:
        total = doc.page_count
        result.total_pages = total
        if total == 0:
            raise JobError("This document has no pages.")

        ctx.set_total(total, "Scanning pages")

        buffer: list[int] = []
        current_date: datetime | None = None
        current_kind = ""
        matched_any = False

        for pno in range(1, total + 1):
            ctx.check_cancel()
            try:
                text = doc[pno - 1].get_text("text") or ""
            except Exception:
                text = ""

            page_date, page_kind = _match_page(text, rules)
            if page_date:
                matched_any = True

            # open the first set
            if current_date is None and page_date:
                current_date, current_kind = page_date, page_kind
                buffer = [pno]
                ctx.step(detail=f"Page {pno}: {page_kind} {page_date:%m/%d/%Y}")
                continue

            # a new date on a page ends the previous set
            if current_date is not None and page_date and page_date != current_date:
                if current_kind == "AIDE":
                    # original behaviour: the boundary page belongs to the new set
                    blocks.append(Visit(current_date, current_kind, buffer.copy()))
                    buffer = [pno]
                else:
                    blocks.append(Visit(current_date, current_kind, buffer.copy()))
                    buffer = [pno]
                current_date, current_kind = page_date, page_kind
                ctx.step(detail=f"Page {pno}: new {page_kind} set")
                continue

            if current_date is not None:
                buffer.append(pno)
            else:
                result.unassigned.append(pno)

            # an end marker closes the set on this page
            if current_date is not None and _closes_set(text, current_kind, rules):
                blocks.append(Visit(current_date, current_kind, buffer.copy()))
                buffer = []
                current_date, current_kind = None, ""

            ctx.step()

        if buffer and current_date is not None:
            blocks.append(Visit(current_date, current_kind, buffer.copy()))
        elif buffer:
            result.unassigned.extend(buffer)

        if not matched_any and use_fallback:
            result.warnings.append(
                "No visit headers matched the configured rules. A generic date "
                "search was used instead — review the results before rebuilding."
            )
            blocks = _fallback_scan(ctx, doc, result)

    if group_same_date:
        blocks = _group(blocks)

    blocks.sort(key=lambda v: v.date or datetime.max)
    result.visits = blocks

    if result.unassigned:
        result.warnings.append(
            f"{len(result.unassigned)} page(s) were not part of any visit: "
            f"{utils.format_page_ranges(result.unassigned)}. They are excluded "
            f"from the rebuilt PDF unless you add them in the table."
        )

    ctx.progress(100, result.message)
    return result


def _fallback_scan(ctx, doc, result: DetectionResult) -> list[Visit]:
    """Generic date detection used when no configured rule matches."""
    blocks: list[Visit] = []
    buffer: list[int] = []
    current: datetime | None = None
    try:
        rx = re.compile(GENERIC_DATE, re.I | re.S)
    except re.error:
        return blocks

    for pno in range(1, doc.page_count + 1):
        try:
            flat = (doc[pno - 1].get_text("text") or "").replace("\n", " ").upper()
        except Exception:
            flat = ""
        m = rx.search(flat)
        dt = _safe_date(m.group(3), m.group(1), m.group(2)) if m else None

        if dt and (current is None or dt != current):
            if buffer and current is not None:
                blocks.append(Visit(current, "VISIT", buffer.copy()))
            buffer = [pno]
            current = dt
        elif current is not None:
            buffer.append(pno)
    if buffer and current is not None:
        blocks.append(Visit(current, "VISIT", buffer.copy()))
    result.unassigned = []
    return blocks


def _group(blocks: Sequence[Visit]) -> list[Visit]:
    """Merge blocks that share a (kind, date) key, counting how many sets merged."""
    grouped: dict[tuple[str, str], Visit] = {}
    loose: list[Visit] = []
    for v in blocks:
        if not v.date:
            loose.append(v)
            continue
        key = (v.kind, v.date.strftime("%Y-%m-%d"))
        if key in grouped:
            grouped[key].pages.extend(v.pages)
            grouped[key].sets += 1
        else:
            grouped[key] = Visit(v.date, v.kind, list(v.pages), 1)
    out = list(grouped.values()) + loose
    for v in out:
        v.pages = sorted(dict.fromkeys(v.pages))
    return out


# ---------------------------------------------------------------------------
# Excel round-trip
# ---------------------------------------------------------------------------

WORKSHEET_HEADERS = ["Visit", "Type", "Date", "Pages", "Set Count", "Page Count", "Note"]


def export_worksheet(visits: Sequence[Visit], output: str | Path) -> Path:
    """Write the detected visits to an Excel file for manual correction."""
    try:
        import openpyxl                                    # type: ignore
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError as e:
        raise JobError("openpyxl is required for the Visit Sorter worksheet.") from e

    out = utils.resolve_collision(Path(output))
    utils.ensure_parent(out)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Visits"
    ws.append(WORKSHEET_HEADERS)

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor=branding.BRAND_ACCENT.lstrip("#"))
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for i, v in enumerate(visits, start=1):
        ws.append([i, v.kind, v.date_text, v.pages_text, v.sets, v.page_count, v.note])

    for col, width in zip("ABCDEFG", (8, 10, 14, 46, 11, 12, 34)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"

    notes = wb.create_sheet("How to edit")
    for row in [
        ["Edit the Date and Pages columns, then reload this file in the app."],
        [""],
        ["Date   — use mm/dd/yyyy, for example 03/12/2026"],
        ["Pages  — page numbers from the ORIGINAL PDF."],
        ["         Accepts 4,5,6 or 4-6 or a mix: 1,3,7,10-15"],
        ["Type   — free text; used only for labelling"],
        [""],
        ["Delete a whole row to leave those pages out of the rebuilt PDF."],
        ["Add a row to include pages the scan missed."],
    ]:
        notes.append(row)
    notes.column_dimensions["A"].width = 78

    wb.save(str(out))
    return out


def load_worksheet(path: str | Path, total_pages: int) -> list[Visit]:
    """Read visits back from the (possibly hand-edited) worksheet."""
    try:
        import openpyxl                                    # type: ignore
    except ImportError as e:
        raise JobError("openpyxl is required to read the worksheet.") from e

    p = Path(path)
    if not p.exists():
        raise JobError(f"Worksheet not found: {p.name}")

    wb = openpyxl.load_workbook(str(p), data_only=True)
    ws = wb["Visits"] if "Visits" in wb.sheetnames else wb.worksheets[0]

    visits: list[Visit] = []
    skipped: list[str] = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        if row is None or all(c is None for c in row):
            continue
        kind = str(row[1] or "").strip() or "VISIT"
        date_raw = row[2]
        pages_raw = row[3]
        sets = int(row[4]) if len(row) > 4 and isinstance(row[4], (int, float)) else 1
        note = str(row[6]).strip() if len(row) > 6 and row[6] else ""

        if not pages_raw:
            continue

        dt = _parse_any_date(date_raw)
        pages = [p for p in utils.parse_page_ranges(str(pages_raw), total_pages)]
        if not pages:
            skipped.append(str(pages_raw))
            continue
        visits.append(Visit(dt, kind, pages, sets, note))

    wb.close()
    if not visits:
        raise JobError("The worksheet contained no usable rows.")
    visits.sort(key=lambda v: v.date or datetime.max)
    return visits


def _parse_any_date(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y",
                "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    m = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", text)
    if m:
        return _safe_date(m.group(3), m.group(1), m.group(2))
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return _safe_date(m.group(1), m.group(2), m.group(3))
    return None


# ---------------------------------------------------------------------------
# rebuild
# ---------------------------------------------------------------------------

@dataclass
class BuildResult:
    outputs: list[Path] = field(default_factory=list)
    pages: int = 0
    visits: int = 0
    parts: int = 0
    message: str = ""
    warnings: list[str] = field(default_factory=list)


def build_sorted_pdf(
    ctx: JobContext,
    source: str | Path,
    visits: Sequence[Visit],
    output: str | Path | None = None,
    password: str | None = None,
    add_bookmarks: bool = True,
) -> BuildResult:
    """Rebuild the PDF with visits in date order."""
    src = Path(source)
    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "sorted", ".pdf")
    )
    utils.ensure_parent(out)
    result = BuildResult()

    ordered = sorted(visits, key=lambda v: v.date or datetime.max)
    new = pymupdf.open()
    toc: list[list] = []

    try:
        with document(src, password) as doc:
            total = doc.page_count
            ctx.set_total(sum(len(v.pages) for v in ordered), "Rebuilding")
            for v in ordered:
                start = new.page_count + 1
                for pno in v.pages:
                    ctx.check_cancel()
                    if not (1 <= pno <= total):
                        result.warnings.append(f"Page {pno} is out of range — skipped")
                        continue
                    new.insert_pdf(doc, from_page=pno - 1, to_page=pno - 1,
                                   annots=True, links=True)
                    ctx.step()
                if add_bookmarks and new.page_count >= start:
                    label = f"{v.kind} — {v.date_text}" if v.date else v.kind
                    toc.append([1, label, start])

        if new.page_count == 0:
            raise JobError("No pages were selected for the rebuilt document.")
        if toc:
            try:
                new.set_toc(toc)
            except Exception:
                pass

        ctx.stage("Saving")
        new.save(str(out), garbage=3, deflate=True)
        result.pages = new.page_count
    finally:
        new.close()

    result.outputs.append(out)
    result.visits = len(ordered)
    result.message = f"Rebuilt {result.pages} page(s) from {result.visits} visit(s) in date order"
    ctx.progress(100, result.message)
    return result


def split_by_size(
    ctx: JobContext,
    source: str | Path,
    visits: Sequence[Visit],
    max_mb: float = 10.0,
    out_dir: str | Path | None = None,
    password: str | None = None,
    name_template: str = "{stem}_Part{index}",
) -> BuildResult:
    """
    Split into parts under a size cap, keeping each visit intact where possible.

    A visit larger than the cap on its own is split across parts page by page
    (the original script's behaviour) and that is reported as a warning, since
    it is the one case where a visit cannot be kept whole.
    """
    src = Path(source)
    folder = Path(out_dir) if out_dir else src.parent / f"{src.stem}_parts"
    folder.mkdir(parents=True, exist_ok=True)
    limit = max(0.05, float(max_mb)) * 1024 * 1024
    result = BuildResult()

    ordered = sorted(visits, key=lambda v: v.date or datetime.max)
    part_index = 1
    current = pymupdf.open()

    def flush():
        nonlocal current, part_index
        if current.page_count == 0:
            return
        name = name_template.format(stem=src.stem, index=part_index)
        target = utils.unique_path(folder / f"{utils.sanitize_filename(name)}.pdf")
        current.save(str(target), garbage=3, deflate=True)
        result.outputs.append(target)
        result.pages += current.page_count
        ctx.log(f"Created {target.name} — {current.page_count} pages, "
                f"{utils.human_size(utils.file_size(target))}")
        current.close()
        current = pymupdf.open()
        part_index += 1

    try:
        with document(src, password) as doc:
            total_pages = doc.page_count
            ctx.set_total(sum(len(v.pages) for v in ordered), "Building parts")

            for v in ordered:
                ctx.check_cancel()
                valid = [p for p in v.pages if 1 <= p <= total_pages]
                if not valid:
                    continue

                probe = pymupdf.open()
                try:
                    for pno in valid:
                        probe.insert_pdf(doc, from_page=pno - 1, to_page=pno - 1)
                    visit_bytes = len(probe.tobytes(garbage=3, deflate=True))
                finally:
                    probe.close()

                if visit_bytes > limit:
                    result.warnings.append(
                        f"{v.kind} {v.date_text} is {utils.human_size(visit_bytes)} on "
                        f"its own — larger than the {max_mb:g} MB limit, so it was "
                        f"split across parts."
                    )
                    for pno in valid:
                        ctx.check_cancel()
                        current.insert_pdf(doc, from_page=pno - 1, to_page=pno - 1)
                        if len(current.tobytes(garbage=1, deflate=True)) > limit and current.page_count > 1:
                            current.delete_page(current.page_count - 1)
                            flush()
                            current.insert_pdf(doc, from_page=pno - 1, to_page=pno - 1)
                        ctx.step()
                    continue

                projected = current.page_count and \
                    (len(current.tobytes(garbage=1, deflate=True)) + visit_bytes > limit)
                if projected:
                    flush()

                for pno in valid:
                    ctx.check_cancel()
                    current.insert_pdf(doc, from_page=pno - 1, to_page=pno - 1,
                                       annots=True, links=True)
                    ctx.step()

            flush()
    finally:
        try:
            current.close()
        except Exception:
            pass

    result.parts = len(result.outputs)
    result.visits = len(ordered)
    if not result.outputs:
        raise JobError("No output parts were produced.")
    result.message = (f"Created {result.parts} part(s) totalling {result.pages} pages, "
                      f"each under {max_mb:g} MB")
    ctx.progress(100, result.message)
    return result
