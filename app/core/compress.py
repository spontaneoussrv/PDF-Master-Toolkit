"""
PDF compression.

Two backends:

**builtin**  (always available)
    Walks every embedded image, downsamples anything above the target DPI,
    re-encodes it as JPEG (or keeps the original when re-encoding would be
    larger), then rewrites the document with object garbage-collection and
    stream deflation. Vector text is never touched, so text stays sharp and
    selectable.

**ghostscript**  (optional, much stronger on scanner output)
    Used when Ghostscript is present and the profile asks for it.

Both backends verify the result: if the "compressed" file is not actually
smaller, the original is kept and the UI is told so. The app never claims a
saving it did not achieve.
"""

from __future__ import annotations

import io
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app import paths
from app.core import deps, utils
from app.core.jobs import JobContext, JobError
from app.core.pdfbase import document, open_pdf, pymupdf

try:
    from PIL import Image
except ImportError:                                # pragma: no cover
    Image = None                                   # type: ignore


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompressionProfile:
    key: str
    label: str
    description: str
    dpi: int
    jpeg_quality: int
    gs_setting: str
    grayscale: bool = False
    prefer_ghostscript: bool = False


PROFILES: dict[str, CompressionProfile] = {
    "low": CompressionProfile(
        "low", "Low compression",
        "Highest quality. Light structural clean-up, images kept near original.",
        dpi=300, jpeg_quality=90, gs_setting="/prepress",
    ),
    "balanced": CompressionProfile(
        "balanced", "Balanced",
        "Recommended. Good quality at a much smaller size — suitable for email.",
        dpi=150, jpeg_quality=72, gs_setting="/ebook",
    ),
    "high": CompressionProfile(
        "high", "High compression",
        "Smallest useful size. Images are noticeably softer when zoomed in.",
        dpi=110, jpeg_quality=55, gs_setting="/screen", prefer_ghostscript=True,
    ),
    "extreme": CompressionProfile(
        "extreme", "Maximum compression",
        "Aggressive. Best for text-heavy scans where readability matters more "
        "than image detail.",
        dpi=80, jpeg_quality=40, gs_setting="/screen",
        grayscale=True, prefer_ghostscript=True,
    ),
    "custom": CompressionProfile(
        "custom", "Custom", "Set the image resolution and quality yourself.",
        dpi=150, jpeg_quality=70, gs_setting="/ebook",
    ),
}

PROFILE_ORDER = ["low", "balanced", "high", "extreme", "custom"]


@dataclass
class CompressOptions:
    profile: str = "balanced"
    dpi: int = 150
    jpeg_quality: int = 72
    grayscale_images: bool = False
    downsample_images: bool = True
    recompress_images: bool = True
    remove_duplicates: bool = True
    clean_structure: bool = True
    remove_metadata: bool = False
    remove_annotations: bool = False
    remove_forms: bool = False
    remove_bookmarks: bool = False
    remove_embedded_files: bool = False
    subset_fonts: bool = True
    linearize: bool = False
    backend: str = "auto"                 # auto | builtin | ghostscript
    min_image_pixels: int = 40_000        # skip icons and rules

    @classmethod
    def from_profile(cls, key: str) -> "CompressOptions":
        p = PROFILES.get(key, PROFILES["balanced"])
        return cls(profile=p.key, dpi=p.dpi, jpeg_quality=p.jpeg_quality,
                   grayscale_images=p.grayscale)


@dataclass
class CompressResult:
    output: Path | None = None
    original_size: int = 0
    compressed_size: int = 0
    pages: int = 0
    images_processed: int = 0
    images_recompressed: int = 0
    backend: str = "builtin"
    warnings: list[str] = field(default_factory=list)
    message: str = ""
    achieved: bool = True

    @property
    def saved_bytes(self) -> int:
        return max(0, self.original_size - self.compressed_size)

    @property
    def saved_percent(self) -> float:
        if not self.original_size:
            return 0.0
        return (self.saved_bytes / self.original_size) * 100.0

    @property
    def ratio_text(self) -> str:
        return f"{self.saved_percent:.1f}%"


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def compress(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    opts: CompressOptions | None = None,
    password: str | None = None,
) -> CompressResult:
    src = Path(source)
    opts = opts or CompressOptions.from_profile("balanced")
    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "compressed", ".pdf")
    )
    utils.ensure_parent(out)

    original_size = utils.file_size(src)
    backend = _choose_backend(opts)

    if backend == "ghostscript":
        result = _compress_ghostscript(ctx, src, out, opts, password)
        if result is None:
            ctx.warn("Ghostscript could not process this file; using the built-in compressor.")
            result = _compress_builtin(ctx, src, out, opts, password)
    else:
        result = _compress_builtin(ctx, src, out, opts, password)

    result.original_size = original_size
    result.compressed_size = utils.file_size(out)

    # Honesty check: never present a bigger file as "compressed".
    if result.compressed_size >= original_size and original_size > 0:
        ctx.warn("Compression did not reduce this file — it is already optimised.")
        try:
            shutil.copyfile(src, out)
            result.compressed_size = utils.file_size(out)
        except OSError:
            pass
        result.achieved = False
        result.message = (
            "This PDF is already well optimised. The output is a copy of the "
            "original at the same size."
        )
    else:
        result.message = (
            f"{utils.human_size(original_size)} → {utils.human_size(result.compressed_size)} "
            f"({result.ratio_text} smaller)"
        )

    result.output = out
    ctx.progress(100, result.message)
    return result


def _choose_backend(opts: CompressOptions) -> str:
    if opts.backend == "builtin":
        return "builtin"
    if opts.backend == "ghostscript":
        return "ghostscript" if deps.has_ghostscript() else "builtin"
    prof = PROFILES.get(opts.profile, PROFILES["balanced"])
    if prof.prefer_ghostscript and deps.has_ghostscript():
        return "ghostscript"
    return "builtin"


# ---------------------------------------------------------------------------
# built-in backend
# ---------------------------------------------------------------------------

def _compress_builtin(ctx, src: Path, out: Path, opts: CompressOptions,
                      password: str | None) -> CompressResult:
    result = CompressResult(backend="builtin")
    doc = open_pdf(src, password)
    try:
        result.pages = doc.page_count
        if opts.downsample_images or opts.recompress_images:
            _shrink_images(ctx, doc, opts, result)

        ctx.stage("Cleaning document structure")
        ctx.progress(88, "Cleaning document structure")

        if opts.remove_metadata:
            try:
                doc.set_metadata({})
                doc.del_xml_metadata()
            except Exception:
                pass
        if opts.remove_bookmarks:
            try:
                doc.set_toc([])
            except Exception:
                pass
        if opts.remove_annotations:
            _strip_annotations(doc)
        if opts.remove_embedded_files:
            _strip_embedded(doc)

        ctx.progress(94, "Writing compressed file")
        save_kwargs = dict(
            garbage=4 if opts.remove_duplicates else 3,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
            clean=bool(opts.clean_structure),
        )
        if opts.subset_fonts:
            try:
                doc.subset_fonts()
            except Exception:
                pass
        if opts.linearize:
            save_kwargs["linear"] = True
        doc.save(str(out), **save_kwargs)
    finally:
        doc.close()
    return result


def _shrink_images(ctx, doc, opts: CompressOptions, result: CompressResult) -> None:
    """Downsample and re-encode embedded raster images in place."""
    if Image is None:
        result.warnings.append("Pillow unavailable — images were left untouched.")
        return

    xrefs: dict[int, list[int]] = {}
    for pno in range(doc.page_count):
        try:
            for info in doc[pno].get_images(full=True):
                xrefs.setdefault(info[0], []).append(pno)
        except Exception:
            continue

    if not xrefs:
        return

    ctx.set_total(len(xrefs), "Optimising images")
    target_dpi = max(30, int(opts.dpi))
    quality = int(utils.clamp(opts.jpeg_quality, 10, 98))

    for xref, pages in xrefs.items():
        ctx.check_cancel()
        result.images_processed += 1
        try:
            raw = doc.extract_image(xref)
        except Exception:
            ctx.step()
            continue
        if not raw or "image" not in raw:
            ctx.step()
            continue

        data = raw["image"]
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception:
            ctx.step()
            continue

        if img.width * img.height < opts.min_image_pixels:
            ctx.step()
            continue

        # Physical size on the page decides the useful resolution.
        display_pt = _display_size_points(doc, pages[0], xref)
        new_size = img.size
        if opts.downsample_images and display_pt:
            wpt, hpt = display_pt
            if wpt > 1 and hpt > 1:
                max_w = max(32, int(wpt / 72.0 * target_dpi))
                max_h = max(32, int(hpt / 72.0 * target_dpi))
                if img.width > max_w or img.height > max_h:
                    scale = min(max_w / img.width, max_h / img.height)
                    new_size = (max(16, int(img.width * scale)),
                                max(16, int(img.height * scale)))

        changed = new_size != img.size
        work = img.resize(new_size, Image.LANCZOS) if changed else img

        has_alpha = work.mode in ("RGBA", "LA") or (
            work.mode == "P" and "transparency" in work.info)

        if opts.grayscale_images and not has_alpha and work.mode != "L":
            work = work.convert("L")
            changed = True
        elif work.mode not in ("RGB", "L") and not has_alpha:
            work = work.convert("RGB")
            changed = True

        if not (changed or opts.recompress_images):
            ctx.step()
            continue

        buf = io.BytesIO()
        try:
            if has_alpha:
                work.save(buf, format="PNG", optimize=True)
            else:
                work.save(buf, format="JPEG", quality=quality,
                          optimize=True, progressive=True)
        except Exception:
            ctx.step()
            continue

        new_data = buf.getvalue()
        if len(new_data) < len(data) * 0.97:
            try:
                _replace_image(doc, xref, new_data)
                result.images_recompressed += 1
            except Exception:
                pass
        ctx.step()


def _replace_image(doc, xref: int, data: bytes) -> None:
    """
    Swap the pixel data of an existing image XObject.

    PyMuPDF's replace_image handles the dictionary rewrite (filter, width,
    height, colorspace) which is why we do not poke the stream directly.
    """
    tmp = Path(tempfile.mkstemp(suffix=".img", dir=str(paths.temp_dir()))[1])
    try:
        tmp.write_bytes(data)
        doc.replace_image(xref, filename=str(tmp))
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _display_size_points(doc, page_index: int, xref: int) -> tuple[float, float] | None:
    try:
        rects = doc[page_index].get_image_rects(xref)
        if rects:
            r = max(rects, key=lambda x: x.width * x.height)
            return (r.width, r.height)
    except Exception:
        pass
    return None


def _strip_annotations(doc) -> None:
    for pno in range(doc.page_count):
        try:
            page = doc[pno]
            for annot in list(page.annots() or []):
                page.delete_annot(annot)
        except Exception:
            continue


def _strip_embedded(doc) -> None:
    try:
        for name in list(doc.embfile_names()):
            doc.embfile_del(name)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ghostscript backend
# ---------------------------------------------------------------------------

def _compress_ghostscript(ctx, src: Path, out: Path, opts: CompressOptions,
                          password: str | None) -> CompressResult | None:
    gs = deps.ghostscript_path()
    if not gs:
        return None

    profile = PROFILES.get(opts.profile, PROFILES["balanced"])
    setting = profile.gs_setting
    dpi = max(30, int(opts.dpi))

    ctx.stage("Compressing with Ghostscript")
    ctx.progress(15, "Compressing with Ghostscript")

    cmd = [
        gs,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.7",
        f"-dPDFSETTINGS={setting}",
        "-dNOPAUSE", "-dBATCH", "-dQUIET", "-dSAFER",
        "-dDetectDuplicateImages=true",
        "-dCompressFonts=true",
        "-dSubsetFonts=true",
        "-dColorImageDownsampleType=/Bicubic",
        f"-dColorImageResolution={dpi}",
        "-dGrayImageDownsampleType=/Bicubic",
        f"-dGrayImageResolution={dpi}",
        "-dMonoImageDownsampleType=/Subsample",
        f"-dMonoImageResolution={max(dpi, 300)}",
        "-dDownsampleColorImages=true",
        "-dDownsampleGrayImages=true",
        "-dAutoRotatePages=/None",
    ]
    if opts.grayscale_images:
        cmd += ["-sColorConversionStrategy=Gray",
                "-dProcessColorModel=/DeviceGray"]
    if password:
        cmd.append(f"-sPDFPassword={password}")
    cmd += [f"-sOutputFile={out}", str(src)]

    try:
        proc = utils.run_cancellable(ctx, cmd, timeout=900)
    except Exception as e:
        ctx.warn(f"Ghostscript failed to start: {e}")
        return None

    if proc.returncode != 0 or not out.exists() or utils.file_size(out) == 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        if detail:
            ctx.warn(f"Ghostscript: {detail[-1][:200]}")
        return None

    ctx.progress(90, "Verifying output")
    result = CompressResult(backend="ghostscript")
    try:
        with document(out) as d:
            result.pages = d.page_count
    except Exception:
        # Ghostscript produced something unreadable -> reject it
        return None
    return result


# ---------------------------------------------------------------------------
# estimation helper for the UI preview
# ---------------------------------------------------------------------------

def estimate(source: str | Path, opts: CompressOptions, sample_pages: int = 4) -> dict:
    """
    Cheap forecast shown before the user commits.

    Only a few pages are examined, so this is explicitly labelled an estimate
    in the UI and never presented as a guarantee.
    """
    src = Path(source)
    info = {"original": utils.file_size(src), "estimated": 0, "confidence": "low",
            "image_bytes": 0, "pages": 0}
    try:
        with document(src) as doc:
            info["pages"] = doc.page_count
            seen: set[int] = set()
            n = min(sample_pages, doc.page_count)
            step = max(1, doc.page_count // max(1, n))
            image_bytes = 0
            for pno in range(0, doc.page_count, step):
                for meta in doc[pno].get_images(full=True):
                    xref = meta[0]
                    if xref in seen:
                        continue
                    seen.add(xref)
                    try:
                        image_bytes += len(doc.extract_image(xref)["image"])
                    except Exception:
                        continue
                if len(seen) > 40:
                    break
            sampled = max(1, len(range(0, doc.page_count, step)))
            scale = doc.page_count / sampled
            info["image_bytes"] = int(image_bytes * scale)
    except Exception:
        return info

    q = utils.clamp(opts.jpeg_quality, 10, 98) / 100.0
    dpi_factor = utils.clamp(opts.dpi / 300.0, 0.15, 1.0) ** 2
    keep = utils.clamp(0.25 + 0.55 * q * dpi_factor, 0.08, 0.95)
    non_image = max(0, info["original"] - info["image_bytes"])
    info["estimated"] = int(non_image * 0.9 + info["image_bytes"] * keep)
    info["confidence"] = "medium" if info["image_bytes"] > info["original"] * 0.4 else "low"
    return info
