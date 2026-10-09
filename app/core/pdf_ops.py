"""
Page-level PDF operations: merge, split, extract, rotate, crop, organize,
insert, normalize page size.

Every public function takes a :class:`~app.core.jobs.JobContext` first so it
can report progress and be cancelled, and returns a small result dataclass the
UI can render without re-reading the file.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from app.core import utils
from app.core.jobs import Cancelled, JobContext, JobError
from app.core.pdfbase import (
    PAGE_SIZES, PdfLocked, document, open_pdf, page_size_points, pymupdf,
    was_encrypted,
)


# ---------------------------------------------------------------------------
# result types
# ---------------------------------------------------------------------------

@dataclass
class OpResult:
    outputs: list[Path] = field(default_factory=list)
    pages: int = 0
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def output(self) -> Path | None:
        return self.outputs[0] if self.outputs else None

    @property
    def total_size(self) -> int:
        return sum(utils.file_size(p) for p in self.outputs)


@dataclass
class MergeItem:
    """One input file in a merge, with an optional page selection."""
    path: Path
    pages: str = ""              # "" == all pages
    password: str | None = None
    rotation: int = 0

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class PdfInfo:
    path: Path
    pages: int = 0
    size: int = 0
    encrypted: bool = False
    error: str = ""
    title: str = ""
    page_size: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


# ---------------------------------------------------------------------------
# inspection
# ---------------------------------------------------------------------------

def inspect(path: str | Path, password: str | None = None) -> PdfInfo:
    """Cheap metadata probe used by every file list in the UI. Never raises."""
    p = Path(path)
    info = PdfInfo(path=p, size=utils.file_size(p))
    try:
        doc = open_pdf(p, password)
    except PdfLocked:
        info.encrypted = True
        info.error = "Password protected"
        return info
    except Exception as e:
        info.error = str(e)
        return info
    try:
        info.pages = doc.page_count
        info.encrypted = was_encrypted(doc)   # never read needs_pass post-auth
        meta = doc.metadata or {}
        info.title = (meta.get("title") or "").strip()
        if doc.page_count:
            r = doc[0].rect
            info.page_size = _describe_size(r.width, r.height)
    except Exception as e:
        info.error = str(e)
    finally:
        doc.close()
    return info


def _describe_size(w: float, h: float, tol: float = 6.0) -> str:
    portrait = w <= h
    pw, ph = (w, h) if portrait else (h, w)
    for name, ps in PAGE_SIZES.items():
        if abs(pw - ps.width) <= tol and abs(ph - ps.height) <= tol:
            return f"{name} {'Portrait' if portrait else 'Landscape'}"
    return f"{w:.0f} × {h:.0f} pt"


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

def merge(
    ctx: JobContext,
    items: Sequence[MergeItem | str | Path],
    output: str | Path,
    bookmark_per_file: bool = True,
    keep_bookmarks: bool = True,
    unique_output: bool = True,
) -> OpResult:
    """Merge documents in the given order, honouring each item's page range."""
    norm: list[MergeItem] = [
        it if isinstance(it, MergeItem) else MergeItem(Path(it)) for it in items
    ]
    if not norm:
        raise JobError("Add at least one PDF to merge.")

    out_path = Path(output)
    if unique_output:
        out_path = utils.resolve_collision(out_path)
    utils.ensure_parent(out_path)

    result = OpResult()
    ctx.set_total(len(norm), "Merging")
    merged = pymupdf.open()
    toc: list[list] = []
    cursor = 0

    try:
        for item in norm:
            ctx.check_cancel()
            ctx.stage(f"Adding {utils.elide(item.name, 40)}")
            try:
                src = open_pdf(item.path, item.password)
            except PdfLocked:
                result.warnings.append(f"{item.name}: skipped — password protected")
                ctx.warn(f"Skipped {item.name}: password protected")
                ctx.step()
                continue
            except Exception as e:
                result.warnings.append(f"{item.name}: skipped — {e}")
                ctx.warn(f"Skipped {item.name}: {e}")
                ctx.step()
                continue

            try:
                total = src.page_count
                if total == 0:
                    result.warnings.append(f"{item.name}: skipped — no pages")
                    continue

                wanted = utils.parse_page_ranges(item.pages, total) if item.pages else list(range(1, total + 1))
                if not wanted:
                    result.warnings.append(f"{item.name}: page range matched no pages")
                    continue

                start_page = merged.page_count

                if _is_contiguous(wanted):
                    merged.insert_pdf(src, from_page=wanted[0] - 1, to_page=wanted[-1] - 1,
                                      annots=True, links=True)
                else:
                    for pno in wanted:
                        merged.insert_pdf(src, from_page=pno - 1, to_page=pno - 1,
                                          annots=True, links=True)

                if item.rotation:
                    for i in range(start_page, merged.page_count):
                        pg = merged[i]
                        pg.set_rotation((pg.rotation + item.rotation) % 360)

                if bookmark_per_file:
                    toc.append([1, item.path.stem, start_page + 1])
                if keep_bookmarks:
                    try:
                        for lvl, title, pno, *_ in (src.get_toc(simple=True) or []):
                            mapped = pno - 1
                            if mapped in [w - 1 for w in wanted]:
                                offset = [w - 1 for w in wanted].index(mapped)
                                toc.append([lvl + (1 if bookmark_per_file else 0),
                                            title, start_page + offset + 1])
                    except Exception:
                        pass

                cursor += len(wanted)
            finally:
                src.close()
            ctx.step()

        if merged.page_count == 0:
            raise JobError("Nothing was merged — every input was skipped or empty.")

        if toc:
            try:
                merged.set_toc(sorted(toc, key=lambda t: t[2]))
            except Exception:
                pass

        ctx.stage("Writing merged document")
        merged.save(str(out_path), garbage=3, deflate=True)
        result.outputs.append(out_path)
        result.pages = merged.page_count
        result.message = f"Merged {len(norm)} file(s) into {merged.page_count} pages"
        ctx.progress(100, "Merge complete")
    finally:
        merged.close()
    return result


def _is_contiguous(pages: Sequence[int]) -> bool:
    return bool(pages) and pages[-1] - pages[0] == len(pages) - 1


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------

SPLIT_MODES = (
    "every_page", "every_n", "ranges", "selected", "before_page", "after_page",
    "by_size", "by_bookmarks", "into_n",
)


def split(
    ctx: JobContext,
    source: str | Path,
    mode: str = "every_page",
    *,
    password: str | None = None,
    every_n: int = 1,
    ranges: str = "",
    selected: str = "",
    at_page: int = 1,
    max_mb: float = 10.0,
    bookmark_level: int = 1,
    parts: int = 2,
    out_dir: str | Path | None = None,
    name_template: str = "{stem}_part{index}",
) -> OpResult:
    """
    Split one PDF into several. ``mode`` selects the strategy; unused
    parameters are ignored, which keeps the UI wiring trivial.
    """
    src_path = Path(source)
    result = OpResult()

    with document(src_path, password) as doc:
        total = doc.page_count
        if total == 0:
            raise JobError("This document has no pages.")

        groups = _plan_split(ctx, doc, src_path, mode, total,
                             every_n=every_n, ranges=ranges, selected=selected,
                             at_page=at_page, max_mb=max_mb,
                             bookmark_level=bookmark_level, parts=parts)
        if not groups:
            raise JobError("The split settings produced no output files.")

        folder = Path(out_dir) if out_dir else src_path.parent / f"{src_path.stem}_split"
        folder.mkdir(parents=True, exist_ok=True)

        ctx.set_total(len(groups), "Writing parts")
        for idx, (label, pages) in enumerate(groups, start=1):
            ctx.check_cancel()
            if not pages:
                continue
            stem = name_template.format(
                stem=src_path.stem, index=idx, label=utils.sanitize_filename(label or f"part{idx}"),
                first=pages[0], last=pages[-1], count=len(pages),
            )
            out = utils.unique_path(folder / f"{utils.sanitize_filename(stem)}.pdf")

            part = pymupdf.open()
            try:
                for pno in pages:
                    part.insert_pdf(doc, from_page=pno - 1, to_page=pno - 1, annots=True, links=True)
                part.save(str(out), garbage=3, deflate=True)
            finally:
                part.close()

            result.outputs.append(out)
            result.pages += len(pages)
            ctx.step(detail=f"{out.name} ({len(pages)} pages)")

    result.message = f"Created {len(result.outputs)} file(s) from {total} pages"
    result.stats["source_pages"] = total
    ctx.progress(100, result.message)
    return result


def _plan_split(ctx, doc, src_path, mode, total, **kw) -> list[tuple[str, list[int]]]:
    """Return [(label, [page numbers])] for the chosen strategy."""
    if mode == "every_page":
        return [(f"page{p}", [p]) for p in range(1, total + 1)]

    if mode == "every_n":
        n = max(1, int(kw.get("every_n", 1)))
        return [(f"{g[0]}-{g[-1]}", g) for g in utils.chunk(list(range(1, total + 1)), n)]

    if mode == "into_n":
        parts = max(1, int(kw.get("parts", 2)))
        per = math.ceil(total / parts)
        return [(f"{g[0]}-{g[-1]}", g) for g in utils.chunk(list(range(1, total + 1)), per)]

    if mode == "ranges":
        groups = []
        for i, token in enumerate([t.strip() for t in str(kw.get("ranges", "")).split("\n") if t.strip()], 1):
            for sub in [s.strip() for s in token.split(";") if s.strip()]:
                pages = utils.parse_page_ranges(sub, total)
                if pages:
                    groups.append((sub.replace(",", "_").replace(" ", ""), pages))
        return groups

    if mode == "selected":
        pages = utils.parse_page_ranges(kw.get("selected", ""), total)
        return [("selected", pages)] if pages else []

    if mode in ("before_page", "after_page"):
        at = utils.clamp(int(kw.get("at_page", 1)), 1, total)
        cut = at - 1 if mode == "before_page" else at
        first = list(range(1, cut + 1))
        second = list(range(cut + 1, total + 1))
        return [g for g in (("part1", first), ("part2", second)) if g[1]]

    if mode == "by_bookmarks":
        level = max(1, int(kw.get("bookmark_level", 1)))
        try:
            toc = doc.get_toc(simple=True) or []
        except Exception:
            toc = []
        marks = [(t[1], t[2]) for t in toc if t[0] <= level and 1 <= t[2] <= total]
        if not marks:
            raise JobError("This PDF has no bookmarks at the selected level.")
        marks.sort(key=lambda m: m[1])
        groups = []
        for i, (title, start) in enumerate(marks):
            end = marks[i + 1][1] - 1 if i + 1 < len(marks) else total
            if end >= start:
                groups.append((title[:60], list(range(start, end + 1))))
        return groups

    if mode == "by_size":
        return _plan_by_size(ctx, doc, total, float(kw.get("max_mb", 10.0)))

    raise JobError(f"Unknown split mode: {mode}")


def _plan_by_size(ctx, doc, total: int, max_mb: float) -> list[tuple[str, list[int]]]:
    """
    Greedy size-based grouping.

    Each page's serialized cost is measured once, then pages are accumulated
    until the estimate would exceed the limit. The estimate is deliberately
    conservative (a fixed structural overhead is added) because PDF objects are
    shared between pages, so the sum of page sizes is an upper bound.
    """
    limit = max(0.05, max_mb) * 1024 * 1024
    ctx.set_total(total, "Measuring pages")
    costs: list[int] = []
    for pno in range(total):
        ctx.check_cancel()
        tmp = pymupdf.open()
        try:
            tmp.insert_pdf(doc, from_page=pno, to_page=pno)
            costs.append(len(tmp.tobytes(garbage=3, deflate=True)))
        except Exception:
            costs.append(0)
        finally:
            tmp.close()
        ctx.step()

    overhead = 2048
    groups: list[tuple[str, list[int]]] = []
    current: list[int] = []
    running = overhead
    for pno in range(1, total + 1):
        c = costs[pno - 1]
        if current and running + c > limit:
            groups.append((f"{current[0]}-{current[-1]}", current))
            current, running = [], overhead
        current.append(pno)
        running += c
        if c > limit and len(current) == 1:
            # a single oversized page cannot be split further
            groups.append((f"{pno}", current))
            current, running = [], overhead
    if current:
        groups.append((f"{current[0]}-{current[-1]}", current))
    return groups


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------

def extract_pages(
    ctx: JobContext,
    source: str | Path,
    pages_spec: str,
    output: str | Path,
    password: str | None = None,
    separate_files: bool = False,
) -> OpResult:
    src = Path(source)
    result = OpResult()
    with document(src, password) as doc:
        total = doc.page_count
        pages = utils.parse_page_ranges(pages_spec, total)
        if not pages:
            raise JobError(f"'{pages_spec}' selected no pages (document has {total}).")

        if separate_files:
            folder = Path(output)
            folder.mkdir(parents=True, exist_ok=True)
            ctx.set_total(len(pages), "Extracting")
            for pno in pages:
                ctx.check_cancel()
                out = utils.unique_path(folder / f"{src.stem}_page{pno}.pdf")
                one = pymupdf.open()
                try:
                    one.insert_pdf(doc, from_page=pno - 1, to_page=pno - 1, annots=True, links=True)
                    one.save(str(out), garbage=3, deflate=True)
                finally:
                    one.close()
                result.outputs.append(out)
                ctx.step()
        else:
            out = utils.resolve_collision(Path(output))
            utils.ensure_parent(out)
            new = pymupdf.open()
            ctx.set_total(len(pages), "Extracting")
            try:
                for pno in pages:
                    ctx.check_cancel()
                    new.insert_pdf(doc, from_page=pno - 1, to_page=pno - 1, annots=True, links=True)
                    ctx.step()
                new.save(str(out), garbage=3, deflate=True)
            finally:
                new.close()
            result.outputs.append(out)

        result.pages = len(pages)
        result.message = f"Extracted {len(pages)} of {total} pages"
    ctx.progress(100, result.message)
    return result


def remove_pages(
    ctx: JobContext,
    source: str | Path,
    pages_spec: str,
    output: str | Path,
    password: str | None = None,
) -> OpResult:
    src = Path(source)
    with document(src, password) as doc:
        total = doc.page_count
        drop = set(utils.parse_page_ranges(pages_spec, total))
        keep = [p for p in range(1, total + 1) if p not in drop]
        if not keep:
            raise JobError("That would remove every page.")
    return extract_pages(ctx, src, utils.format_page_ranges(keep), output, password)


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------

def rotate(
    ctx: JobContext,
    source: str | Path,
    angle: int,
    output: str | Path,
    pages_spec: str = "all",
    password: str | None = None,
    absolute: bool = False,
) -> OpResult:
    """Rotate pages by ``angle`` (relative) or set rotation to it (absolute)."""
    src = Path(source)
    out = utils.resolve_collision(Path(output))
    utils.ensure_parent(out)
    angle = int(angle) % 360
    result = OpResult()

    with document(src, password) as doc:
        total = doc.page_count
        pages = utils.parse_page_ranges(pages_spec, total)
        if not pages:
            raise JobError("No pages selected to rotate.")
        ctx.set_total(len(pages), "Rotating")
        for pno in pages:
            ctx.check_cancel()
            page = doc[pno - 1]
            page.set_rotation(angle if absolute else (page.rotation + angle) % 360)
            ctx.step()
        ctx.stage("Saving")
        doc.save(str(out), garbage=3, deflate=True)
        result.pages = len(pages)

    result.outputs.append(out)
    result.message = f"Rotated {len(pages)} page(s) by {angle}°"
    ctx.progress(100, result.message)
    return result


# ---------------------------------------------------------------------------
# crop
# ---------------------------------------------------------------------------

def crop(
    ctx: JobContext,
    source: str | Path,
    output: str | Path,
    *,
    margins: tuple[float, float, float, float] | None = None,   # l, t, r, b in points
    box: tuple[float, float, float, float] | None = None,       # absolute rect on page 1
    relative_box: tuple[float, float, float, float] | None = None,  # 0..1 fractions
    pages_spec: str = "all",
    password: str | None = None,
) -> OpResult:
    """
    Crop pages. Exactly one of margins / box / relative_box should be given;
    ``relative_box`` is what the interactive crop tool sends because it stays
    correct across pages of differing sizes.
    """
    src = Path(source)
    out = utils.resolve_collision(Path(output))
    utils.ensure_parent(out)
    result = OpResult()

    with document(src, password) as doc:
        total = doc.page_count
        pages = utils.parse_page_ranges(pages_spec, total)
        if not pages:
            raise JobError("No pages selected to crop.")
        ctx.set_total(len(pages), "Cropping")

        for pno in pages:
            ctx.check_cancel()
            page = doc[pno - 1]
            r = page.rect

            if relative_box:
                x0 = r.x0 + relative_box[0] * r.width
                y0 = r.y0 + relative_box[1] * r.height
                x1 = r.x0 + relative_box[2] * r.width
                y1 = r.y0 + relative_box[3] * r.height
            elif box:
                x0, y0, x1, y1 = box
            elif margins:
                l, t, rr, b = margins
                x0, y0, x1, y1 = r.x0 + l, r.y0 + t, r.x1 - rr, r.y1 - b
            else:
                raise JobError("No crop area specified.")

            new = pymupdf.Rect(
                max(r.x0, min(x0, x1)), max(r.y0, min(y0, y1)),
                min(r.x1, max(x0, x1)), min(r.y1, max(y0, y1)),
            )
            if new.width < 12 or new.height < 12:
                result.warnings.append(f"Page {pno}: crop area too small, left unchanged")
                ctx.step()
                continue
            try:
                page.set_cropbox(new)
            except Exception as e:
                result.warnings.append(f"Page {pno}: {e}")
            ctx.step()

        ctx.stage("Saving")
        doc.save(str(out), garbage=3, deflate=True)
        result.pages = len(pages)

    result.outputs.append(out)
    result.message = f"Cropped {len(pages)} page(s)"
    ctx.progress(100, result.message)
    return result


def auto_crop_margins(
    ctx: JobContext,
    source: str | Path,
    output: str | Path,
    padding: float = 6.0,
    pages_spec: str = "all",
    password: str | None = None,
) -> OpResult:
    """Trim white borders by measuring each page's ink extent."""
    src = Path(source)
    out = utils.resolve_collision(Path(output))
    utils.ensure_parent(out)
    result = OpResult()

    with document(src, password) as doc:
        total = doc.page_count
        pages = utils.parse_page_ranges(pages_spec, total)
        ctx.set_total(len(pages), "Detecting content")
        for pno in pages:
            ctx.check_cancel()
            page = doc[pno - 1]
            bbox = _content_bbox(page)
            if bbox is None:
                result.warnings.append(f"Page {pno}: no content detected, left unchanged")
                ctx.step()
                continue
            r = page.rect
            new = pymupdf.Rect(
                max(r.x0, bbox.x0 - padding), max(r.y0, bbox.y0 - padding),
                min(r.x1, bbox.x1 + padding), min(r.y1, bbox.y1 + padding),
            )
            if new.width > 12 and new.height > 12:
                try:
                    page.set_cropbox(new)
                except Exception:
                    pass
            ctx.step()
        ctx.stage("Saving")
        doc.save(str(out), garbage=3, deflate=True)
        result.pages = len(pages)

    result.outputs.append(out)
    result.message = f"Auto-cropped {result.pages} page(s)"
    ctx.progress(100, result.message)
    return result


def _content_bbox(page):
    """Union of text and drawing bounds; None when the page is blank."""
    rects = []
    try:
        for block in page.get_text("blocks") or []:
            rects.append(pymupdf.Rect(block[:4]))
    except Exception:
        pass
    try:
        for d in page.get_drawings() or []:
            rects.append(d["rect"])
    except Exception:
        pass
    try:
        for img in page.get_images(full=True):
            for r in page.get_image_rects(img[0]):
                rects.append(r)
    except Exception:
        pass
    rects = [r for r in rects if r.width > 0.5 and r.height > 0.5]
    if not rects:
        return None
    out = rects[0]
    for r in rects[1:]:
        out |= r
    return out


# ---------------------------------------------------------------------------
# organize (apply an explicit page plan)
# ---------------------------------------------------------------------------

@dataclass
class PageSlot:
    """One entry in an organizer plan."""
    source: int | None          # 0-based index in the source doc; None = blank/insert
    rotation: int = 0
    blank_size: tuple[float, float] | None = None
    insert_path: Path | None = None
    insert_index: int = 0
    image_path: Path | None = None


def apply_page_plan(
    ctx: JobContext,
    source: str | Path,
    plan: Sequence[PageSlot],
    output: str | Path,
    password: str | None = None,
) -> OpResult:
    """
    Rebuild a document from an ordered plan. This is what the visual Organize
    Pages screen commits — reorder, delete, duplicate, rotate, insert blank
    pages, insert other PDFs and insert images all reduce to one plan.
    """
    src = Path(source)
    out = utils.resolve_collision(Path(output))
    utils.ensure_parent(out)
    result = OpResult()

    if not plan:
        raise JobError("The document would have no pages.")

    cache: dict[str, object] = {}
    new = pymupdf.open()
    try:
        with document(src, password) as doc:
            ctx.set_total(len(plan), "Building document")
            for slot in plan:
                ctx.check_cancel()
                if slot.source is not None:
                    if not (0 <= slot.source < doc.page_count):
                        result.warnings.append(f"Skipped missing source page {slot.source + 1}")
                        ctx.step()
                        continue
                    new.insert_pdf(doc, from_page=slot.source, to_page=slot.source,
                                   annots=True, links=True)
                elif slot.insert_path:
                    key = str(slot.insert_path)
                    other = cache.get(key)
                    if other is None:
                        other = open_pdf(slot.insert_path)
                        cache[key] = other
                    idx = utils.clamp(slot.insert_index, 0, other.page_count - 1)  # type: ignore[union-attr]
                    new.insert_pdf(other, from_page=idx, to_page=idx, annots=True, links=True)
                elif slot.image_path:
                    _insert_image_page(new, slot.image_path, slot.blank_size)
                else:
                    w, h = slot.blank_size or (595.0, 842.0)
                    new.new_page(width=w, height=h)

                if slot.rotation:
                    pg = new[new.page_count - 1]
                    pg.set_rotation((pg.rotation + slot.rotation) % 360)
                ctx.step()

        if new.page_count == 0:
            raise JobError("The plan produced an empty document.")

        ctx.stage("Saving")
        new.save(str(out), garbage=3, deflate=True)
        result.pages = new.page_count
    finally:
        new.close()
        for d in cache.values():
            try:
                d.close()  # type: ignore[union-attr]
            except Exception:
                pass

    result.outputs.append(out)
    result.message = f"Saved {result.pages} pages"
    ctx.progress(100, result.message)
    return result


def _insert_image_page(doc, image_path: Path, size: tuple[float, float] | None):
    w, h = size or (595.0, 842.0)
    page = doc.new_page(width=w, height=h)
    rect = pymupdf.Rect(0, 0, w, h)
    try:
        page.insert_image(rect, filename=str(image_path), keep_proportion=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# page size normalisation
# ---------------------------------------------------------------------------

def normalize_page_size(
    ctx: JobContext,
    source: str | Path,
    output: str | Path,
    page_size: str = "A4",
    orientation: str = "auto",       # auto | portrait | landscape | match
    mode: str = "fit",               # fit | fill | stretch
    margin: float = 0.0,
    password: str | None = None,
) -> OpResult:
    """
    Rescale every page onto a uniform sheet.

    ``auto`` orientation keeps each page's own portrait/landscape character,
    which is what you want for a mixed scan; ``match`` forces the first page's
    orientation onto the whole document.
    """
    src = Path(source)
    out = utils.resolve_collision(Path(output))
    utils.ensure_parent(out)
    result = OpResult()

    with document(src, password) as doc:
        total = doc.page_count
        if total == 0:
            raise JobError("This document has no pages.")

        first = doc[0].rect
        forced_landscape = first.width > first.height

        new = pymupdf.open()
        ctx.set_total(total, "Resizing pages")
        try:
            for i in range(total):
                ctx.check_cancel()
                srcpage = doc[i]
                r = srcpage.rect

                if orientation == "portrait":
                    landscape = False
                elif orientation == "landscape":
                    landscape = True
                elif orientation == "match":
                    landscape = forced_landscape
                else:
                    landscape = r.width > r.height

                tw, th = page_size_points(page_size, landscape)
                target = new.new_page(width=tw, height=th)

                avail = pymupdf.Rect(margin, margin, tw - margin, th - margin)
                if avail.width <= 0 or avail.height <= 0:
                    avail = pymupdf.Rect(0, 0, tw, th)

                if mode == "stretch":
                    dest = avail
                else:
                    sx = avail.width / r.width
                    sy = avail.height / r.height
                    scale = max(sx, sy) if mode == "fill" else min(sx, sy)
                    w, h = r.width * scale, r.height * scale
                    cx = avail.x0 + (avail.width - w) / 2
                    cy = avail.y0 + (avail.height - h) / 2
                    dest = pymupdf.Rect(cx, cy, cx + w, cy + h)

                target.show_pdf_page(dest, doc, i, rotate=0)
                ctx.step()

            ctx.stage("Saving")
            new.save(str(out), garbage=3, deflate=True)
            result.pages = new.page_count
        finally:
            new.close()

    result.outputs.append(out)
    result.message = f"Resized {result.pages} page(s) to {page_size}"
    ctx.progress(100, result.message)
    return result


# ---------------------------------------------------------------------------
# n-up / booklet
# ---------------------------------------------------------------------------

def n_up(
    ctx: JobContext,
    source: str | Path,
    output: str | Path,
    cols: int = 2,
    rows: int = 1,
    page_size: str = "A4",
    landscape: bool = True,
    gap: float = 8.0,
    margin: float = 18.0,
    password: str | None = None,
) -> OpResult:
    """Place several source pages on each output sheet."""
    src = Path(source)
    out = utils.resolve_collision(Path(output))
    utils.ensure_parent(out)
    per = max(1, cols) * max(1, rows)
    result = OpResult()

    with document(src, password) as doc:
        total = doc.page_count
        tw, th = page_size_points(page_size, landscape)
        new = pymupdf.open()
        sheets = math.ceil(total / per)
        ctx.set_total(total, "Composing")
        try:
            for sheet in range(sheets):
                ctx.check_cancel()
                target = new.new_page(width=tw, height=th)
                cell_w = (tw - 2 * margin - gap * (cols - 1)) / cols
                cell_h = (th - 2 * margin - gap * (rows - 1)) / rows
                for slot in range(per):
                    idx = sheet * per + slot
                    if idx >= total:
                        break
                    c, r = slot % cols, slot // cols
                    x0 = margin + c * (cell_w + gap)
                    y0 = margin + r * (cell_h + gap)
                    src_rect = doc[idx].rect
                    scale = min(cell_w / src_rect.width, cell_h / src_rect.height)
                    w, h = src_rect.width * scale, src_rect.height * scale
                    dest = pymupdf.Rect(
                        x0 + (cell_w - w) / 2, y0 + (cell_h - h) / 2,
                        x0 + (cell_w - w) / 2 + w, y0 + (cell_h - h) / 2 + h,
                    )
                    target.show_pdf_page(dest, doc, idx)
                    ctx.step()
            ctx.stage("Saving")
            new.save(str(out), garbage=3, deflate=True)
            result.pages = new.page_count
        finally:
            new.close()

    result.outputs.append(out)
    result.message = f"{cols}×{rows} layout — {result.pages} sheet(s)"
    ctx.progress(100, result.message)
    return result


# ---------------------------------------------------------------------------
# quick single-file helpers used by batch + workflow
# ---------------------------------------------------------------------------

def rewrite(
    ctx: JobContext,
    source: str | Path,
    output: str | Path,
    linearize: bool = False,
    password: str | None = None,
) -> OpResult:
    """Clean rewrite: garbage-collect objects, deflate streams, optional linearize."""
    out = utils.resolve_collision(Path(output))
    utils.ensure_parent(out)
    with document(source, password) as doc:
        ctx.progress(40, "Rewriting document")
        kwargs = dict(garbage=4, deflate=True, clean=True)
        if linearize:
            kwargs["linear"] = True
        doc.save(str(out), **kwargs)
        pages = doc.page_count
    ctx.progress(100, "Done")
    return OpResult(outputs=[out], pages=pages, message="Document rewritten")
