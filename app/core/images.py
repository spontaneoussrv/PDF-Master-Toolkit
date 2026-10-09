"""
PDF ↔ image conversion and image extraction.

  * :func:`pdf_to_images`  — rasterise pages to PNG / JPG / WEBP / TIFF
  * :func:`images_to_pdf`  — assemble images into a PDF with page fitting
  * :func:`extract_images` — pull the *embedded* images out of a PDF
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from app.core import utils
from app.core.jobs import JobContext, JobError
from app.core.pdfbase import document, page_size_points, pymupdf

try:
    from PIL import Image, ImageOps
except ImportError:                                # pragma: no cover
    Image = None                                   # type: ignore


IMAGE_FORMATS = {
    "png":  {"label": "PNG (lossless)", "ext": ".png", "pil": "PNG", "alpha": True},
    "jpg":  {"label": "JPEG (small)", "ext": ".jpg", "pil": "JPEG", "alpha": False},
    "webp": {"label": "WEBP (modern)", "ext": ".webp", "pil": "WEBP", "alpha": True},
    "tiff": {"label": "TIFF (archival)", "ext": ".tif", "pil": "TIFF", "alpha": True},
    "bmp":  {"label": "BMP (uncompressed)", "ext": ".bmp", "pil": "BMP", "alpha": False},
}

DPI_PRESETS = [72, 96, 150, 200, 300, 400, 600]


# ---------------------------------------------------------------------------
# PDF -> images
# ---------------------------------------------------------------------------

@dataclass
class RasterOptions:
    fmt: str = "png"
    dpi: int = 150
    pages: str = "all"
    quality: int = 90                  # JPEG / WEBP
    grayscale: bool = False
    transparent: bool = False          # PNG / WEBP only
    antialias: bool = True
    name_template: str = "{stem}_page{page:03d}"
    single_tiff: bool = False          # multipage TIFF instead of one file per page


@dataclass
class RasterResult:
    outputs: list[Path] = field(default_factory=list)
    pages: int = 0
    folder: Path | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)


def pdf_to_images(
    ctx: JobContext,
    source: str | Path,
    out_dir: str | Path | None = None,
    opts: RasterOptions | None = None,
    password: str | None = None,
) -> RasterResult:
    src = Path(source)
    opts = opts or RasterOptions()
    spec = IMAGE_FORMATS.get(opts.fmt, IMAGE_FORMATS["png"])
    folder = Path(out_dir) if out_dir else src.parent / f"{src.stem}_images"
    folder.mkdir(parents=True, exist_ok=True)

    result = RasterResult(folder=folder)
    zoom = max(24, int(opts.dpi)) / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    frames: list = []

    with document(src, password) as doc:
        total = doc.page_count
        pages = utils.parse_page_ranges(opts.pages, total)
        if not pages:
            raise JobError("The selected page range contains no pages.")
        ctx.set_total(len(pages), "Rendering pages")

        for pno in pages:
            ctx.check_cancel()
            page = doc[pno - 1]
            alpha = bool(opts.transparent and spec["alpha"])
            try:
                cs = pymupdf.csGRAY if opts.grayscale else pymupdf.csRGB
                pix = page.get_pixmap(matrix=matrix, alpha=alpha, colorspace=cs)
            except Exception as e:
                result.warnings.append(f"Page {pno}: {e}")
                ctx.step()
                continue

            name = opts.name_template.format(stem=src.stem, page=pno, total=total)
            target = folder / f"{utils.sanitize_filename(name)}{spec['ext']}"

            if opts.fmt == "tiff" and opts.single_tiff:
                frames.append(_pix_to_pil(pix))
            elif opts.fmt in ("png",) and not opts.grayscale:
                target = utils.unique_path(target)
                pix.save(str(target))
                _stamp_dpi(target, opts.dpi)
                result.outputs.append(target)
            else:
                target = utils.unique_path(target)
                img = _pix_to_pil(pix)
                _save_pil(img, target, spec["pil"], opts)
                result.outputs.append(target)

            result.pages += 1
            ctx.step(detail=f"Page {pno}")

        if opts.fmt == "tiff" and opts.single_tiff and frames:
            target = utils.unique_path(folder / f"{utils.sanitize_filename(src.stem)}.tif")
            frames[0].save(str(target), format="TIFF", save_all=True,
                           append_images=frames[1:], compression="tiff_deflate",
                           dpi=(opts.dpi, opts.dpi))
            result.outputs.append(target)

    result.message = f"Exported {result.pages} page(s) as {opts.fmt.upper()}"
    ctx.progress(100, result.message)
    return result


def _pix_to_pil(pix):
    if Image is None:
        raise JobError("Pillow is required for image export.")
    mode = "RGBA" if pix.alpha else ("L" if pix.n == 1 else "RGB")
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def _save_pil(img, path: Path, pil_format: str, opts: RasterOptions) -> None:
    kwargs: dict = {"dpi": (opts.dpi, opts.dpi)}
    if pil_format == "JPEG":
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = bg
        kwargs.update(quality=int(utils.clamp(opts.quality, 10, 98)),
                      optimize=True, progressive=True)
    elif pil_format == "WEBP":
        kwargs.update(quality=int(utils.clamp(opts.quality, 10, 100)), method=4)
        kwargs.pop("dpi", None)
    elif pil_format == "TIFF":
        kwargs.update(compression="tiff_deflate")
    elif pil_format == "BMP":
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        kwargs.pop("dpi", None)
    img.save(str(path), format=pil_format, **kwargs)


def _stamp_dpi(path: Path, dpi: int) -> None:
    """PyMuPDF writes PNGs without a pHYs chunk; add one so print size is right."""
    if Image is None:
        return
    try:
        img = Image.open(path)
        img.save(str(path), format="PNG", dpi=(dpi, dpi))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# images -> PDF
# ---------------------------------------------------------------------------

FIT_MODES = {
    "fit": "Fit to page (keep proportions)",
    "fill": "Fill page (crop overflow)",
    "stretch": "Stretch to page",
    "original": "Original image size",
}


@dataclass
class ImageItem:
    path: Path
    rotation: int = 0
    crop: tuple[float, float, float, float] | None = None    # relative 0..1

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class ImagesToPdfOptions:
    page_size: str = "A4"                # or "original"
    landscape: bool = False
    auto_orient: bool = True             # per-image orientation from aspect ratio
    fit: str = "fit"
    margin: float = 24.0                 # points
    quality: int = 88
    grayscale: bool = False
    max_dimension: int = 0               # 0 = no downscale
    background: str = "#FFFFFF"


def images_to_pdf(
    ctx: JobContext,
    images: Sequence[ImageItem | str | Path],
    output: str | Path,
    opts: ImagesToPdfOptions | None = None,
) -> RasterResult:
    if Image is None:
        raise JobError("Pillow is required to build a PDF from images.")
    opts = opts or ImagesToPdfOptions()
    items = [i if isinstance(i, ImageItem) else ImageItem(Path(i)) for i in images]
    if not items:
        raise JobError("Add at least one image.")

    out = utils.resolve_collision(Path(output))
    utils.ensure_parent(out)
    result = RasterResult()
    doc = pymupdf.open()

    try:
        ctx.set_total(len(items), "Adding images")
        for item in items:
            ctx.check_cancel()
            try:
                img = Image.open(item.path)
                img.load()
            except Exception as e:
                result.warnings.append(f"{item.name}: skipped ({e})")
                ctx.step()
                continue

            img = ImageOps.exif_transpose(img) or img
            if item.rotation:
                img = img.rotate(-item.rotation, expand=True, fillcolor="white")
            if item.crop:
                l, t, r, b = item.crop
                box = (int(l * img.width), int(t * img.height),
                       int(r * img.width), int(b * img.height))
                if box[2] - box[0] > 4 and box[3] - box[1] > 4:
                    img = img.crop(box)
            if opts.grayscale and img.mode != "L":
                img = img.convert("L")
            if opts.max_dimension and max(img.size) > opts.max_dimension:
                scale = opts.max_dimension / max(img.size)
                img = img.resize((max(1, int(img.width * scale)),
                                  max(1, int(img.height * scale))), Image.LANCZOS)

            # page geometry
            if opts.page_size == "original":
                dpi = img.info.get("dpi", (96, 96))
                dx = float(dpi[0] or 96)
                pw = img.width * 72.0 / dx
                ph = img.height * 72.0 / dx
                if pw < 20 or ph < 20 or pw > 14400 or ph > 14400:
                    pw, ph = float(img.width), float(img.height)
                page = doc.new_page(width=pw, height=ph)
                dest = pymupdf.Rect(0, 0, pw, ph)
            else:
                landscape = opts.landscape
                if opts.auto_orient:
                    landscape = img.width > img.height
                pw, ph = page_size_points(opts.page_size, landscape)
                page = doc.new_page(width=pw, height=ph)
                m = max(0.0, opts.margin)
                area = pymupdf.Rect(m, m, pw - m, ph - m)
                if area.width <= 4 or area.height <= 4:
                    area = pymupdf.Rect(0, 0, pw, ph)
                dest = _fit_rect(area, img.width, img.height, opts.fit)

            buf = io.BytesIO()
            if img.mode in ("RGBA", "LA", "P"):
                # Flatten transparency onto the chosen background: PDF pages are
                # opaque, so an un-flattened alpha channel reads as black.
                bg = Image.new("RGB", img.size, opts.background)
                conv = img.convert("RGBA")
                bg.paste(conv, mask=conv.split()[-1])
                img = bg
            elif img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=int(utils.clamp(opts.quality, 20, 98)),
                     optimize=True)

            try:
                page.insert_image(dest, stream=buf.getvalue(),
                                  keep_proportion=(opts.fit != "stretch"))
            except Exception as e:
                result.warnings.append(f"{item.name}: could not be placed ({e})")

            result.pages += 1
            ctx.step(detail=item.name)

        if doc.page_count == 0:
            raise JobError("None of the selected images could be processed.")
        ctx.stage("Saving PDF")
        doc.save(str(out), garbage=3, deflate=True)
        result.outputs.append(out)
    finally:
        doc.close()

    result.message = f"Created a {result.pages}-page PDF from {len(items)} image(s)"
    ctx.progress(100, result.message)
    return result


def _fit_rect(area, iw: int, ih: int, mode: str):
    if mode == "stretch":
        return area
    if mode == "original":
        w = min(float(iw), area.width)
        h = min(float(ih), area.height)
        cx = area.x0 + (area.width - w) / 2
        cy = area.y0 + (area.height - h) / 2
        return pymupdf.Rect(cx, cy, cx + w, cy + h)
    sx, sy = area.width / iw, area.height / ih
    scale = max(sx, sy) if mode == "fill" else min(sx, sy)
    w, h = iw * scale, ih * scale
    cx = area.x0 + (area.width - w) / 2
    cy = area.y0 + (area.height - h) / 2
    return pymupdf.Rect(cx, cy, cx + w, cy + h)


# ---------------------------------------------------------------------------
# extract embedded images
# ---------------------------------------------------------------------------

@dataclass
class ExtractImagesOptions:
    pages: str = "all"
    fmt: str = "original"              # original | png | jpg
    min_width: int = 64
    min_height: int = 64
    min_bytes: int = 2048
    skip_duplicates: bool = True
    name_template: str = "{stem}_p{page:03d}_img{index:02d}"


def extract_images(
    ctx: JobContext,
    source: str | Path,
    out_dir: str | Path | None = None,
    opts: ExtractImagesOptions | None = None,
    password: str | None = None,
) -> RasterResult:
    src = Path(source)
    opts = opts or ExtractImagesOptions()
    folder = Path(out_dir) if out_dir else src.parent / f"{src.stem}_images"
    folder.mkdir(parents=True, exist_ok=True)
    result = RasterResult(folder=folder)

    seen: set[int] = set()
    with document(src, password) as doc:
        total = doc.page_count
        pages = utils.parse_page_ranges(opts.pages, total)
        ctx.set_total(len(pages), "Scanning pages")
        counter = 0

        for pno in pages:
            ctx.check_cancel()
            try:
                infos = doc[pno - 1].get_images(full=True)
            except Exception:
                ctx.step()
                continue

            for idx, meta in enumerate(infos, start=1):
                xref = meta[0]
                if opts.skip_duplicates and xref in seen:
                    continue
                seen.add(xref)
                try:
                    raw = doc.extract_image(xref)
                except Exception:
                    continue
                if not raw:
                    continue
                data = raw.get("image", b"")
                ext = (raw.get("ext") or "png").lower()
                w, h = int(raw.get("width", 0)), int(raw.get("height", 0))

                if w < opts.min_width or h < opts.min_height or len(data) < opts.min_bytes:
                    continue

                counter += 1
                stem = opts.name_template.format(stem=src.stem, page=pno,
                                                 index=idx, xref=xref)
                stem = utils.sanitize_filename(stem)

                if opts.fmt == "original":
                    target = utils.unique_path(folder / f"{stem}.{ext}")
                    target.write_bytes(data)
                else:
                    spec = IMAGE_FORMATS.get(opts.fmt, IMAGE_FORMATS["png"])
                    target = utils.unique_path(folder / f"{stem}{spec['ext']}")
                    if Image is None:
                        target = utils.unique_path(folder / f"{stem}.{ext}")
                        target.write_bytes(data)
                    else:
                        try:
                            img = Image.open(io.BytesIO(data))
                            img.load()
                            if spec["pil"] == "JPEG" and img.mode not in ("RGB", "L"):
                                img = img.convert("RGB")
                            img.save(str(target), format=spec["pil"])
                        except Exception:
                            target = utils.unique_path(folder / f"{stem}.{ext}")
                            target.write_bytes(data)

                result.outputs.append(target)
            ctx.step(detail=f"Page {pno} — {len(result.outputs)} image(s) so far")

    result.pages = len(result.outputs)
    if not result.outputs:
        result.message = ("No embedded images matched the filters. Scanned PDFs "
                          "sometimes store each page as one large image — lower "
                          "the minimum size if you expected results.")
    else:
        result.message = f"Extracted {len(result.outputs)} image(s)"
    ctx.progress(100, result.message)
    return result
