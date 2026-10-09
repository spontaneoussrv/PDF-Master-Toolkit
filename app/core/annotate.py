"""
Content stamping: watermarks, headers and footers, page numbers, and
visual / cryptographic signatures.

All four share the same placement grid and variable substitution, so a change
to positioning logic applies consistently across the app.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.core import deps, utils
from app.core.jobs import JobContext, JobError
from app.core.pdfbase import document, pymupdf

# ---------------------------------------------------------------------------
# shared placement
# ---------------------------------------------------------------------------

POSITIONS = [
    "top-left", "top-center", "top-right",
    "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
]

FONTS = {
    "Helvetica": "helv",
    "Helvetica Bold": "hebo",
    "Helvetica Oblique": "heit",
    "Times": "tiro",
    "Times Bold": "tibo",
    "Times Italic": "tiit",
    "Courier": "cour",
    "Courier Bold": "cobo",
}
FONT_ORDER = list(FONTS.keys())


def _anchor(rect, position: str, w: float, h: float, margin: float) -> tuple[float, float]:
    """Top-left corner for a w×h box placed at ``position`` inside ``rect``."""
    vert, _, horiz = position.partition("-")
    if position == "center":
        vert, horiz = "middle", "center"

    if horiz == "left":
        x = rect.x0 + margin
    elif horiz == "right":
        x = rect.x1 - margin - w
    else:
        x = rect.x0 + (rect.width - w) / 2

    if vert == "top":
        y = rect.y0 + margin
    elif vert == "bottom":
        y = rect.y1 - margin - h
    else:
        y = rect.y0 + (rect.height - h) / 2
    return x, y


def substitute(template: str, *, page: int, total: int, filename: str,
               title: str = "", author: str = "", extra: dict | None = None) -> str:
    """
    Expand the variables offered in Header & Footer and Page Numbers.

    Both brace and bare-word forms are accepted because users type both:
        "Page {page} of {total}"   and   "Page [page] of [total]"
    """
    now = datetime.now()
    values = {
        "page": str(page),
        "pages": str(total),
        "total": str(total),
        "totalpages": str(total),
        "filename": filename,
        "file": filename,
        "name": Path(filename).stem,
        "title": title,
        "author": author,
        "date": now.strftime("%d %b %Y"),
        "date_iso": now.strftime("%Y-%m-%d"),
        "date_us": now.strftime("%m/%d/%Y"),
        "time": now.strftime("%H:%M"),
        "time12": now.strftime("%I:%M %p"),
        "datetime": now.strftime("%d %b %Y %H:%M"),
        "year": now.strftime("%Y"),
        "month": now.strftime("%B"),
        "day": now.strftime("%d"),
        "roman": utils.to_roman(page),
        "ROMAN": utils.to_roman(page, upper=True),
        "letter": utils.to_letters(page),
    }
    if extra:
        values.update({k: str(v) for k, v in extra.items()})

    out = template or ""
    for key, val in values.items():
        out = out.replace("{" + key + "}", val)
        out = out.replace("[" + key + "]", val)
    return out


VARIABLE_HELP = [
    ("{page}", "Current page number"),
    ("{total}", "Total page count"),
    ("{filename}", "File name with extension"),
    ("{name}", "File name without extension"),
    ("{title}", "Document title from metadata"),
    ("{author}", "Document author from metadata"),
    ("{date}", "Today's date (12 Mar 2026)"),
    ("{date_iso}", "Today's date (2026-03-12)"),
    ("{time}", "Current time (14:05)"),
    ("{roman}", "Page number in roman numerals (iv)"),
    ("{ROMAN}", "Page number in roman numerals (IV)"),
]


def hex_to_rgb01(value: str) -> tuple[float, float, float]:
    v = (value or "#000000").lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    try:
        return (int(v[0:2], 16) / 255, int(v[2:4], 16) / 255, int(v[4:6], 16) / 255)
    except ValueError:
        return (0.0, 0.0, 0.0)


@dataclass
class StampResult:
    output: Path | None = None
    pages: int = 0
    message: str = ""
    warnings: list[str] = field(default_factory=list)


# ===========================================================================
# WATERMARK
# ===========================================================================

WATERMARK_PRESETS = ["CONFIDENTIAL", "DRAFT", "COPY", "SAMPLE", "DO NOT COPY",
                     "INTERNAL USE ONLY", "APPROVED", "EXPIRED", "ORIGINAL"]


@dataclass
class WatermarkOptions:
    kind: str = "text"                       # text | image
    text: str = "CONFIDENTIAL"
    image_path: Path | None = None

    font: str = "Helvetica Bold"
    font_size: int = 60
    auto_size: bool = True                   # fit diagonal across the page
    color: str = "#FF0000"
    opacity: float = 0.18
    rotation: float = 45.0
    position: str = "center"
    margin: float = 36.0
    scale: float = 0.55                      # image width as a fraction of page
    behind: bool = True                      # under the page content
    tile: bool = False
    tile_gap: float = 220.0
    pages: str = "all"
    skip_first: bool = False


def watermark(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    opts: WatermarkOptions | None = None,
    password: str | None = None,
) -> StampResult:
    src = Path(source)
    opts = opts or WatermarkOptions()
    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "watermarked", ".pdf")
    )
    utils.ensure_parent(out)
    result = StampResult()

    if opts.kind == "image":
        if not opts.image_path or not Path(opts.image_path).is_file():
            raise JobError("Choose a watermark image file.")
    elif not (opts.text or "").strip():
        raise JobError("Enter the watermark text.")

    color = hex_to_rgb01(opts.color)
    fontname = FONTS.get(opts.font, "hebo")
    opacity = float(utils.clamp(opts.opacity, 0.02, 1.0))

    with document(src, password) as doc:
        total = doc.page_count
        pages = utils.parse_page_ranges(opts.pages, total)
        if opts.skip_first:
            pages = [p for p in pages if p != 1]
        if not pages:
            raise JobError("No pages selected for the watermark.")

        ctx.set_total(len(pages), "Applying watermark")
        for pno in pages:
            ctx.check_cancel()
            page = doc[pno - 1]
            try:
                if opts.kind == "image":
                    _stamp_image(page, opts, opacity)
                else:
                    _stamp_text(page, opts, fontname, color, opacity)
            except Exception as e:
                result.warnings.append(f"Page {pno}: {e}")
            ctx.step(detail=f"Page {pno}")

        ctx.stage("Saving")
        doc.save(str(out), garbage=3, deflate=True)
        result.pages = len(pages)

    result.output = out
    result.message = f"Watermark applied to {result.pages} page(s)"
    ctx.progress(100, result.message)
    return result


def _stamp_text(page, opts: WatermarkOptions, fontname: str,
                color: tuple[float, float, float], opacity: float) -> None:
    rect = page.rect
    text = opts.text

    size = float(opts.font_size)
    if opts.auto_size:
        try:
            unit = pymupdf.get_text_length(text, fontname=fontname, fontsize=1.0)
        except Exception:
            unit = 0.5 * len(text)
        if unit > 0:
            diagonal = math.hypot(rect.width, rect.height) * 0.78
            size = utils.clamp(diagonal / unit, 8.0, 400.0)

    if opts.tile:
        step = max(60.0, float(opts.tile_gap))
        y = rect.y0 - rect.height * 0.2
        row = 0
        while y < rect.y1 + rect.height * 0.2:
            x = rect.x0 - rect.width * 0.2 + (step * 0.5 if row % 2 else 0)
            while x < rect.x1 + rect.width * 0.2:
                _write_rotated(page, (x, y), text, fontname, min(size, step * 0.4),
                               color, opacity, opts.rotation, opts.behind)
                x += step
            y += step
            row += 1
        return

    try:
        width = pymupdf.get_text_length(text, fontname=fontname, fontsize=size)
    except Exception:
        width = size * 0.5 * len(text)
    height = size

    x, y = _anchor(rect, opts.position, width, height, opts.margin)
    _write_rotated(page, (x, y + height * 0.78), text, fontname, size,
                   color, opacity, opts.rotation, opts.behind)


def _write_rotated(page, point, text, fontname, size, color, opacity,
                   rotation, behind) -> None:
    """
    Draw one run of watermark text.

    A rotated stamp is drawn through a Shape with a rotation morph anchored at
    the text origin, which keeps the glyph baseline centred on the anchor point
    rather than swinging it off the page.
    """
    px, py = point
    pivot = pymupdf.Point(px, py)
    try:
        shape = page.new_shape()
        shape.insert_text(
            pivot, text, fontname=fontname, fontsize=size,
            color=color, fill=color, fill_opacity=opacity, stroke_opacity=opacity,
            morph=(pivot, pymupdf.Matrix(rotation)) if rotation else None,
            overlay=not behind,
        )
        shape.commit(overlay=not behind)
    except Exception:
        page.insert_text(pivot, text, fontname=fontname, fontsize=size,
                         color=color, fill_opacity=opacity, overlay=not behind)


def _stamp_image(page, opts: WatermarkOptions, opacity: float) -> None:
    rect = page.rect
    path = Path(opts.image_path)  # type: ignore[arg-type]

    try:
        from PIL import Image
        with Image.open(path) as im:
            iw, ih = im.size
    except Exception:
        iw, ih = 1000, 1000

    target_w = rect.width * float(utils.clamp(opts.scale, 0.05, 1.0))
    target_h = target_w * (ih / max(1, iw))
    if target_h > rect.height * 0.9:
        target_h = rect.height * 0.9
        target_w = target_h * (iw / max(1, ih))

    x, y = _anchor(rect, opts.position, target_w, target_h, opts.margin)
    dest = pymupdf.Rect(x, y, x + target_w, y + target_h)

    kwargs = dict(filename=str(path), keep_proportion=True, overlay=not opts.behind)
    if opts.rotation:
        kwargs["rotate"] = int(round(opts.rotation / 90.0) * 90) % 360
    try:
        page.insert_image(dest, alpha=-1, **kwargs)
    except TypeError:
        page.insert_image(dest, **kwargs)


# ===========================================================================
# HEADER & FOOTER
# ===========================================================================

@dataclass
class HeaderFooterOptions:
    header_left: str = ""
    header_center: str = ""
    header_right: str = ""
    footer_left: str = ""
    footer_center: str = "Page {page} of {total}"
    footer_right: str = ""

    font: str = "Helvetica"
    font_size: float = 9.0
    color: str = "#444444"
    margin: float = 28.0
    pages: str = "all"
    skip_first: bool = False
    start_number: int = 1
    different_odd_even: bool = False
    draw_rule: bool = False
    rule_color: str = "#DDDDDD"


def header_footer(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    opts: HeaderFooterOptions | None = None,
    password: str | None = None,
) -> StampResult:
    src = Path(source)
    opts = opts or HeaderFooterOptions()
    slots = [opts.header_left, opts.header_center, opts.header_right,
             opts.footer_left, opts.footer_center, opts.footer_right]
    if not any(s.strip() for s in slots):
        raise JobError("Enter text for at least one header or footer position.")

    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "headed", ".pdf")
    )
    utils.ensure_parent(out)

    fontname = FONTS.get(opts.font, "helv")
    color = hex_to_rgb01(opts.color)
    rule_color = hex_to_rgb01(opts.rule_color)
    result = StampResult()

    with document(src, password) as doc:
        total = doc.page_count
        pages = utils.parse_page_ranges(opts.pages, total)
        if opts.skip_first:
            pages = [p for p in pages if p != 1]
        meta = doc.metadata or {}
        ctx.set_total(len(pages), "Adding headers and footers")

        for pno in pages:
            ctx.check_cancel()
            page = doc[pno - 1]
            rect = page.rect
            number = opts.start_number + (pno - 1)
            swap = opts.different_odd_even and pno % 2 == 0

            entries = [
                (opts.header_right if swap else opts.header_left, "left", "top"),
                (opts.header_center, "center", "top"),
                (opts.header_left if swap else opts.header_right, "right", "top"),
                (opts.footer_right if swap else opts.footer_left, "left", "bottom"),
                (opts.footer_center, "center", "bottom"),
                (opts.footer_left if swap else opts.footer_right, "right", "bottom"),
            ]

            for template, horiz, vert in entries:
                if not (template or "").strip():
                    continue
                text = substitute(template, page=number, total=total,
                                  filename=src.name, title=meta.get("title", ""),
                                  author=meta.get("author", ""))
                _place_line(page, rect, text, horiz, vert, fontname,
                            opts.font_size, color, opts.margin)

            if opts.draw_rule:
                if any((opts.header_left, opts.header_center, opts.header_right)):
                    y = rect.y0 + opts.margin + opts.font_size * 0.6
                    page.draw_line(pymupdf.Point(rect.x0 + opts.margin, y),
                                   pymupdf.Point(rect.x1 - opts.margin, y),
                                   color=rule_color, width=0.6)
                if any((opts.footer_left, opts.footer_center, opts.footer_right)):
                    y = rect.y1 - opts.margin - opts.font_size * 1.4
                    page.draw_line(pymupdf.Point(rect.x0 + opts.margin, y),
                                   pymupdf.Point(rect.x1 - opts.margin, y),
                                   color=rule_color, width=0.6)

            ctx.step(detail=f"Page {pno}")

        ctx.stage("Saving")
        doc.save(str(out), garbage=3, deflate=True)
        result.pages = len(pages)

    result.output = out
    result.message = f"Header/footer added to {result.pages} page(s)"
    ctx.progress(100, result.message)
    return result


def _place_line(page, rect, text: str, horiz: str, vert: str, fontname: str,
                size: float, color, margin: float) -> None:
    try:
        width = pymupdf.get_text_length(text, fontname=fontname, fontsize=size)
    except Exception:
        width = size * 0.5 * len(text)

    if horiz == "left":
        x = rect.x0 + margin
    elif horiz == "right":
        x = rect.x1 - margin - width
    else:
        x = rect.x0 + (rect.width - width) / 2

    y = (rect.y0 + margin + size) if vert == "top" else (rect.y1 - margin)

    try:
        page.insert_text((x, y), text, fontname=fontname, fontsize=size,
                         color=color, overlay=True)
    except Exception:
        page.insert_text((x, y), text, fontname="helv", fontsize=size,
                         color=color, overlay=True)


# ===========================================================================
# PAGE NUMBERS
# ===========================================================================

NUMBER_FORMATS = {
    "plain": ("1", "{page}"),
    "of_total": ("1 of 25", "{page} of {total}"),
    "page_n": ("Page 1", "Page {page}"),
    "page_n_of_m": ("Page 1 of 25", "Page {page} of {total}"),
    "dashes": ("- 1 -", "- {page} -"),
    "roman_lower": ("i, ii, iii", "{roman}"),
    "roman_upper": ("I, II, III", "{ROMAN}"),
    "letters": ("A, B, C", "{letter}"),
    "bracket": ("[1]", "[{page}]"),
    "slash": ("1/25", "{page}/{total}"),
}
NUMBER_FORMAT_ORDER = list(NUMBER_FORMATS.keys())


@dataclass
class PageNumberOptions:
    fmt: str = "page_n_of_m"
    custom: str = ""
    position: str = "bottom-center"
    font: str = "Helvetica"
    font_size: float = 10.0
    color: str = "#333333"
    margin: float = 30.0
    start_number: int = 1
    first_numbered_page: int = 1
    pages: str = "all"
    skip_first: bool = False
    box: bool = False
    box_color: str = "#F0F0F0"


def page_numbers(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    opts: PageNumberOptions | None = None,
    password: str | None = None,
) -> StampResult:
    src = Path(source)
    opts = opts or PageNumberOptions()
    template = opts.custom.strip() or NUMBER_FORMATS.get(opts.fmt, NUMBER_FORMATS["plain"])[1]

    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "numbered", ".pdf")
    )
    utils.ensure_parent(out)

    fontname = FONTS.get(opts.font, "helv")
    color = hex_to_rgb01(opts.color)
    box_color = hex_to_rgb01(opts.box_color)
    result = StampResult()

    with document(src, password) as doc:
        total = doc.page_count
        pages = utils.parse_page_ranges(opts.pages, total)
        pages = [p for p in pages if p >= opts.first_numbered_page]
        if opts.skip_first:
            pages = [p for p in pages if p != 1]
        if not pages:
            raise JobError("No pages selected for numbering.")

        numbered_total = len(pages) if "{total}" in template else total
        ctx.set_total(len(pages), "Numbering pages")

        for offset, pno in enumerate(pages):
            ctx.check_cancel()
            page = doc[pno - 1]
            rect = page.rect
            number = opts.start_number + offset
            text = substitute(template, page=number, total=numbered_total,
                              filename=src.name)

            try:
                width = pymupdf.get_text_length(text, fontname=fontname,
                                                fontsize=opts.font_size)
            except Exception:
                width = opts.font_size * 0.55 * len(text)
            height = opts.font_size

            x, y = _anchor(rect, opts.position, width, height * 1.2, opts.margin)

            if opts.box:
                pad = 4.0
                page.draw_rect(
                    pymupdf.Rect(x - pad, y - pad, x + width + pad, y + height + pad),
                    color=None, fill=box_color, radius=0.25,
                )
            try:
                page.insert_text((x, y + height * 0.85), text, fontname=fontname,
                                 fontsize=opts.font_size, color=color, overlay=True)
            except Exception:
                page.insert_text((x, y + height * 0.85), text, fontname="helv",
                                 fontsize=opts.font_size, color=color, overlay=True)
            ctx.step(detail=f"Page {pno}")

        ctx.stage("Saving")
        doc.save(str(out), garbage=3, deflate=True)
        result.pages = len(pages)

    result.output = out
    result.message = f"Numbered {result.pages} page(s)"
    ctx.progress(100, result.message)
    return result


# ===========================================================================
# SIGNATURES
# ===========================================================================

SIGNATURE_DISCLAIMER = (
    "A visual signature is a picture of a signature placed on the page. It is "
    "not a cryptographic digital signature and does not prove who signed the "
    "document or detect later changes. Use certificate signing for that."
)


@dataclass
class SignaturePlacement:
    page: int                                   # 1-based
    rect: tuple[float, float, float, float]     # PDF points


@dataclass
class VisualSignOptions:
    image_path: Path | None = None
    text: str = ""
    font: str = "Helvetica Oblique"
    font_size: float = 22.0
    color: str = "#12305C"
    placements: list[SignaturePlacement] = field(default_factory=list)
    add_date: bool = False
    date_text: str = "Signed {date_iso}"
    date_size: float = 7.5
    flatten: bool = True


def sign_visual(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    opts: VisualSignOptions | None = None,
    password: str | None = None,
) -> StampResult:
    src = Path(source)
    opts = opts or VisualSignOptions()
    if not opts.placements:
        raise JobError("Place the signature on the page first.")
    if not opts.image_path and not (opts.text or "").strip():
        raise JobError("Choose a signature image or enter signature text.")

    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "signed", ".pdf")
    )
    utils.ensure_parent(out)

    result = StampResult()
    color = hex_to_rgb01(opts.color)
    fontname = FONTS.get(opts.font, "heit")

    with document(src, password) as doc:
        ctx.set_total(len(opts.placements), "Placing signature")
        for place in opts.placements:
            ctx.check_cancel()
            if not (1 <= place.page <= doc.page_count):
                result.warnings.append(f"Page {place.page} does not exist — skipped")
                ctx.step()
                continue
            page = doc[place.page - 1]
            rect = pymupdf.Rect(*place.rect)
            if rect.width < 4 or rect.height < 4:
                ctx.step()
                continue

            if opts.image_path and Path(opts.image_path).is_file():
                try:
                    page.insert_image(rect, filename=str(opts.image_path),
                                      keep_proportion=True, overlay=True, alpha=-1)
                except TypeError:
                    page.insert_image(rect, filename=str(opts.image_path),
                                      keep_proportion=True, overlay=True)
            else:
                size = opts.font_size
                try:
                    width = pymupdf.get_text_length(opts.text, fontname=fontname, fontsize=size)
                    if width > rect.width and width > 0:
                        size = size * (rect.width / width) * 0.95
                except Exception:
                    pass
                page.insert_text((rect.x0, rect.y0 + rect.height * 0.68), opts.text,
                                 fontname=fontname, fontsize=size, color=color,
                                 overlay=True)

            if opts.add_date:
                stamp = substitute(opts.date_text, page=place.page,
                                   total=doc.page_count, filename=src.name)
                page.insert_text((rect.x0, rect.y1 + opts.date_size * 1.3), stamp,
                                 fontname="helv", fontsize=opts.date_size,
                                 color=(0.35, 0.38, 0.44), overlay=True)

            result.pages += 1
            ctx.step(detail=f"Page {place.page}")

        ctx.stage("Saving")
        doc.save(str(out), garbage=3, deflate=True)

    result.output = out
    result.message = f"Signature placed on {result.pages} page(s)"
    result.warnings.append(SIGNATURE_DISCLAIMER)
    ctx.progress(100, result.message)
    return result


# ---------------------------------------------------------------------------
# certificate-based signing
# ---------------------------------------------------------------------------

@dataclass
class CertificateSignOptions:
    pfx_path: Path | None = None
    pfx_password: str = ""
    reason: str = ""
    location: str = ""
    contact: str = ""
    field_name: str = "Signature1"
    page: int = 1
    rect: tuple[float, float, float, float] | None = None
    visible: bool = True


def sign_certificate(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    opts: CertificateSignOptions | None = None,
) -> StampResult:
    """
    Cryptographically sign a PDF with a PKCS#12 (.pfx / .p12) certificate.

    Requires the optional pyHanko package. When it is unavailable the caller is
    told exactly that, rather than silently falling back to a visual stamp,
    because the two are not interchangeable.
    """
    if not deps.has_pyhanko():
        raise JobError(
            "Certificate signing needs the pyHanko component, which is not "
            "installed in this build.\n\n"
            "Visual signatures are available in the Sign PDF tool and work "
            "offline, but they are not cryptographic signatures."
        )

    opts = opts or CertificateSignOptions()
    src = Path(source)
    if not opts.pfx_path or not Path(opts.pfx_path).is_file():
        raise JobError("Choose a .pfx or .p12 certificate file.")

    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "signed", ".pdf")
    )
    utils.ensure_parent(out)

    ctx.stage("Loading certificate")
    ctx.progress(20, "Loading certificate")

    try:
        from pyhanko.sign import signers                      # type: ignore
        from pyhanko.sign.fields import SigFieldSpec, append_signature_field  # type: ignore
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter  # type: ignore

        signer = signers.SimpleSigner.load_pkcs12(
            pfx_file=str(opts.pfx_path),
            passphrase=(opts.pfx_password or "").encode() or None,
        )
        if signer is None:
            raise JobError("The certificate could not be loaded. Check the password.")

        ctx.progress(50, "Signing document")
        with open(src, "rb") as fh:
            writer = IncrementalPdfFileWriter(fh)
            if opts.visible and opts.rect:
                append_signature_field(
                    writer,
                    SigFieldSpec(sig_field_name=opts.field_name,
                                 on_page=max(0, opts.page - 1),
                                 box=opts.rect),
                )
            meta = signers.PdfSignatureMetadata(
                field_name=opts.field_name,
                reason=opts.reason or None,
                location=opts.location or None,
                contact_info=opts.contact or None,
            )
            with open(out, "wb") as outfh:
                signers.sign_pdf(writer, meta, signer=signer, output=outfh)
    except JobError:
        raise
    except Exception as e:
        raise JobError(f"Signing failed: {e}") from e

    ctx.progress(100, "Document signed")
    return StampResult(output=out, pages=1,
                       message="Document signed with your certificate")
