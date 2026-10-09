"""
OCR engine.

Two text-layer strategies, both offline and both driven by Tesseract:

**native** (default)
    The original page is left completely untouched. Each page is rendered to an
    image only to *read* it; the recognised words are then written back onto the
    original page as invisible glyphs (PDF render mode 3). The result looks
    pixel-identical to the input, but Ctrl+F, text selection and copy all work.

**rebuild**
    The page is replaced by Tesseract's own image+text PDF. Visual fidelity is
    slightly lower (the page becomes the preprocessed render) but every script
    Tesseract supports is covered, because Tesseract embeds its own fonts. This
    is selected automatically when the requested language needs glyphs the
    available fonts cannot draw.

Nothing here talks to the network, and no temporary file survives a run.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from app import config, paths
from app.core import deps, utils
from app.core.jobs import Cancelled, JobContext, JobError
from app.core.pdfbase import document, open_pdf, page_has_text, pymupdf

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
except ImportError:                                        # pragma: no cover
    Image = None                                           # type: ignore

try:
    import numpy as np
except ImportError:                                        # pragma: no cover
    np = None                                              # type: ignore


# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------

OCR_MODES = ("searchable", "text", "both")

# Scripts with no glyph coverage in PyMuPDF's built-in fonts. When one of these
# is requested and no bundled font file covers it, we fall back to 'rebuild'.
_NON_LATIN_SCRIPTS = {
    "hin": "devanagari", "mar": "devanagari", "nep": "devanagari", "san": "devanagari",
    "ben": "bengali", "asm": "bengali",
    "tam": "tamil", "tel": "telugu", "kan": "kannada", "mal": "malayalam",
    "guj": "gujarati", "pan": "gurmukhi", "ori": "oriya", "sin": "sinhala",
    "ara": "arabic", "urd": "arabic", "fas": "arabic", "heb": "hebrew",
    "tha": "thai", "khm": "khmer", "lao": "lao", "mya": "myanmar",
    "chi_sim": "cjk", "chi_tra": "cjk", "jpn": "cjk", "kor": "cjk",
    "rus": "cyrillic", "ukr": "cyrillic", "bul": "cyrillic", "srp": "cyrillic",
    "ell": "greek",
}

# PyMuPDF ships these, so they need no bundled file.
_BUILTIN_FONT_FOR = {
    "cjk": "china-s",
    "cyrillic": "helv",     # Base-14 Helvetica covers Cyrillic via WinAnsi poorly;
    "greek": "helv",        # treated as covered only if the glyph test passes
}

_SCRIPT_FONT_FILES = {
    "devanagari": ["NotoSansDevanagari-Regular.ttf"],
    "bengali": ["NotoSansBengali-Regular.ttf"],
    "tamil": ["NotoSansTamil-Regular.ttf"],
    "telugu": ["NotoSansTelugu-Regular.ttf"],
    "kannada": ["NotoSansKannada-Regular.ttf"],
    "malayalam": ["NotoSansMalayalam-Regular.ttf"],
    "gujarati": ["NotoSansGujarati-Regular.ttf"],
    "gurmukhi": ["NotoSansGurmukhi-Regular.ttf"],
    "oriya": ["NotoSansOriya-Regular.ttf"],
    "sinhala": ["NotoSansSinhala-Regular.ttf"],
    "arabic": ["NotoSansArabic-Regular.ttf"],
    "hebrew": ["NotoSansHebrew-Regular.ttf"],
    "thai": ["NotoSansThai-Regular.ttf"],
    "khmer": ["NotoSansKhmer-Regular.ttf"],
    "lao": ["NotoSansLao-Regular.ttf"],
    "myanmar": ["NotoSansMyanmar-Regular.ttf"],
    "cyrillic": ["NotoSans-Regular.ttf"],
    "greek": ["NotoSans-Regular.ttf"],
    "latin": ["NotoSans-Regular.ttf"],
}


@dataclass
class OcrOptions:
    languages: list[str] = field(default_factory=lambda: ["eng"])
    dpi: int = 300
    mode: str = "searchable"                # searchable | text | both
    pages: str = "all"

    # preprocessing
    grayscale: bool = True
    deskew: bool = True
    auto_rotate: bool = True
    denoise: bool = False
    contrast: bool = False
    binarize: bool = False
    upscale_small: bool = True              # bump low-DPI scans before recognition

    # engine
    psm: int = 3
    oem: int = 3
    text_layer: str = "auto"                # auto | native | rebuild
    skip_pages_with_text: bool = True
    min_confidence: int = 0                 # drop words below this confidence

    # output
    side_text_file: bool = False

    @property
    def lang_arg(self) -> str:
        return "+".join(self.languages) if self.languages else "eng"

    @classmethod
    def from_settings(cls) -> "OcrOptions":
        return cls(
            languages=list(config.get_list("ocr_languages", ["eng"])) or ["eng"],
            dpi=config.get_int("ocr_dpi", 300),
            mode=config.get("ocr_mode", "searchable"),
            grayscale=config.get_bool("ocr_grayscale", True),
            deskew=config.get_bool("ocr_deskew", True),
            auto_rotate=config.get_bool("ocr_rotate", True),
            denoise=config.get_bool("ocr_denoise", False),
            contrast=config.get_bool("ocr_contrast", False),
            binarize=config.get_bool("ocr_binarize", False),
            psm=config.get_int("ocr_psm", 3),
            oem=config.get_int("ocr_oem", 3),
            skip_pages_with_text=config.get_bool("ocr_skip_text_pages", True),
        )


@dataclass
class OcrWord:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float
    line: int = 0
    block: int = 0

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class OcrPageResult:
    index: int
    words: list[OcrWord] = field(default_factory=list)
    text: str = ""
    skipped: bool = False
    reason: str = ""
    rotation_applied: int = 0
    skew_corrected: float = 0.0
    mean_confidence: float = 0.0


@dataclass
class OcrResult:
    outputs: list[Path] = field(default_factory=list)
    text: str = ""
    pages: list[OcrPageResult] = field(default_factory=list)
    page_count: int = 0
    ocred_pages: int = 0
    skipped_pages: int = 0
    warnings: list[str] = field(default_factory=list)
    engine: str = "native"
    message: str = ""

    @property
    def output(self) -> Path | None:
        return self.outputs[0] if self.outputs else None

    @property
    def mean_confidence(self) -> float:
        vals = [p.mean_confidence for p in self.pages if p.mean_confidence > 0]
        return sum(vals) / len(vals) if vals else 0.0


# ---------------------------------------------------------------------------
# tesseract plumbing
# ---------------------------------------------------------------------------

def _require_tesseract() -> str:
    exe = deps.tesseract_path()
    if not exe:
        raise JobError(
            "Tesseract OCR was not found on this computer.\n\n"
            "Reinstall PDF Master Toolkit with the OCR component enabled, or "
            "point Settings → OCR → Tesseract location at an existing install."
        )
    return exe


def check_languages(langs: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split requested languages into (available, missing)."""
    installed = set(deps.installed_languages())
    if not installed:
        return list(langs), []
    have = [l for l in langs if l in installed]
    missing = [l for l in langs if l not in installed]
    return have, missing


def _tess_cmd(exe: str, image: Path, out_base: str, opts: OcrOptions, mode: str) -> list[str]:
    cmd = [
        exe, str(image), out_base,
        "-l", opts.lang_arg,
        "--psm", str(opts.psm),
        "--oem", str(opts.oem),
        "--dpi", str(max(70, opts.dpi)),
        "-c", "tessedit_do_invert=0",
    ]
    if mode == "tsv":
        cmd.append("tsv")
    elif mode == "pdf":
        cmd.append("pdf")
    elif mode == "txt":
        pass                       # default output is .txt
    return cmd


def _run_tesseract(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, errors="replace",
            timeout=timeout, env=deps.tesseract_env(), **utils.no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as e:
        raise JobError("Tesseract timed out on a page. Try a lower OCR resolution.") from e
    except FileNotFoundError as e:
        raise JobError("Tesseract could not be launched. Check the path in Settings.") from e


def _parse_tsv(tsv_text: str, min_conf: int = 0) -> list[OcrWord]:
    words: list[OcrWord] = []
    lines = tsv_text.splitlines()
    if not lines:
        return words
    header = lines[0].split("\t")
    try:
        ix = {name: header.index(name) for name in
              ("left", "top", "width", "height", "conf", "text", "line_num", "block_num")}
    except ValueError:
        return words

    for raw in lines[1:]:
        parts = raw.split("\t")
        if len(parts) <= ix["text"]:
            continue
        text = parts[ix["text"]].strip()
        if not text:
            continue
        try:
            conf = float(parts[ix["conf"]])
            if conf < min_conf or conf < 0:
                continue
            left = float(parts[ix["left"]])
            top = float(parts[ix["top"]])
            w = float(parts[ix["width"]])
            h = float(parts[ix["height"]])
            line = int(parts[ix["line_num"]])
            block = int(parts[ix["block_num"]])
        except (ValueError, IndexError):
            continue
        if w <= 0 or h <= 0:
            continue
        words.append(OcrWord(text, left, top, left + w, top + h, conf, line, block))
    return words


def detect_orientation(image_path: Path) -> int:
    """Return the clockwise rotation (0/90/180/270) Tesseract thinks is needed."""
    exe = deps.tesseract_path()
    if not exe:
        return 0
    try:
        r = subprocess.run(
            [exe, str(image_path), "-", "--psm", "0", "-l", "osd"],
            capture_output=True, text=True, errors="replace", timeout=60,
            env=deps.tesseract_env(), **utils.no_window_kwargs(),
        )
        m = re.search(r"Rotate:\s*(\d+)", r.stdout or "")
        if m:
            return int(m.group(1)) % 360
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# image preprocessing
# ---------------------------------------------------------------------------

def preprocess(img, opts: OcrOptions) -> tuple:
    """Return (processed_image, skew_angle_applied)."""
    if Image is None:
        return img, 0.0

    skew = 0.0
    if opts.grayscale or opts.binarize or opts.deskew:
        if img.mode not in ("L", "1"):
            img = img.convert("L")

    if opts.contrast:
        img = ImageOps.autocontrast(img, cutoff=1)
        img = ImageEnhance.Contrast(img).enhance(1.35)

    if opts.denoise:
        img = img.filter(ImageFilter.MedianFilter(size=3))

    if opts.deskew:
        angle = estimate_skew(img)
        if abs(angle) >= 0.15:
            img = img.rotate(angle, resample=Image.BICUBIC, expand=True,
                             fillcolor=255 if img.mode == "L" else None)
            skew = angle

    if opts.binarize:
        img = binarize(img)

    if opts.upscale_small and max(img.size) < 1200:
        scale = 1200 / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)

    return img, skew


def estimate_skew(img, max_angle: float = 6.0, step: float = 0.25) -> float:
    """
    Projection-profile skew detection.

    The image is downscaled hard first — skew is a global property, so a 700px
    wide proxy gives the same answer for a fraction of the work.
    """
    if np is None or Image is None:
        return 0.0
    try:
        small = img.convert("L")
        if small.width > 700:
            ratio = 700 / small.width
            small = small.resize((700, max(50, int(small.height * ratio))), Image.BILINEAR)
        arr = np.asarray(small, dtype=np.float32)
        if arr.size == 0:
            return 0.0
        # ink = dark pixels
        thresh = arr.mean() * 0.85
        binary = (arr < thresh).astype(np.float32)
        if binary.sum() < 40:
            return 0.0

        best_angle, best_score = 0.0, -1.0
        proxy = Image.fromarray((binary * 255).astype("uint8"))
        angle = -max_angle
        while angle <= max_angle + 1e-6:
            rotated = proxy.rotate(angle, resample=Image.NEAREST, fillcolor=0)
            a = np.asarray(rotated, dtype=np.float32) / 255.0
            profile = a.sum(axis=1)
            score = float(np.var(profile))
            if score > best_score:
                best_score, best_angle = score, angle
            angle += step
        return round(best_angle, 2)
    except Exception:
        return 0.0


def binarize(img):
    """Otsu threshold when numpy is present, fixed threshold otherwise."""
    if Image is None:
        return img
    if img.mode != "L":
        img = img.convert("L")
    if np is None:
        return img.point(lambda p: 255 if p > 176 else 0, mode="L")
    try:
        arr = np.asarray(img)
        hist, _ = np.histogram(arr, bins=256, range=(0, 256))
        total = arr.size
        sum_all = float((np.arange(256) * hist).sum())
        sum_b = 0.0
        w_b = 0
        best_t, best_var = 128, -1.0
        for t in range(256):
            w_b += int(hist[t])
            if w_b == 0:
                continue
            w_f = total - w_b
            if w_f == 0:
                break
            sum_b += t * float(hist[t])
            m_b = sum_b / w_b
            m_f = (sum_all - sum_b) / w_f
            var = w_b * w_f * (m_b - m_f) ** 2
            if var > best_var:
                best_var, best_t = var, t
        return img.point(lambda p, t=best_t: 255 if p > t else 0, mode="L")
    except Exception:
        return img.point(lambda p: 255 if p > 176 else 0, mode="L")


def _pixmap_to_pil(pix):
    if Image is None:
        raise JobError("Pillow is required for OCR but is not installed.")
    mode = "RGB" if pix.n < 4 else "RGBA"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


# ---------------------------------------------------------------------------
# font selection for the invisible text layer
# ---------------------------------------------------------------------------

def _scripts_for(langs: Iterable[str]) -> set[str]:
    return {_NON_LATIN_SCRIPTS.get(l, "latin") for l in langs}


def _find_font_file(script: str) -> Path | None:
    for name in _SCRIPT_FONT_FILES.get(script, []):
        p = paths.assets_dir() / "fonts" / name
        if p.is_file():
            return p
    return None


def _load_font(script: str):
    """Return (pymupdf.Font, fontname, fontfile|None) or None if unavailable."""
    f = _find_font_file(script)
    if f is not None:
        try:
            return pymupdf.Font(fontfile=str(f)), f"ocr-{script}", str(f)
        except Exception:
            pass
    builtin = _BUILTIN_FONT_FOR.get(script)
    if builtin:
        try:
            return pymupdf.Font(builtin), builtin, None
        except Exception:
            pass
    if script in ("latin", "cyrillic", "greek"):
        try:
            return pymupdf.Font("helv"), "helv", None
        except Exception:
            pass
    return None


def resolve_text_layer(opts: OcrOptions) -> tuple[str, list[str]]:
    """
    Decide between 'native' and 'rebuild'.

    Returns (engine, notes). 'auto' picks native whenever every requested
    language has a usable font, because native preserves the original page
    exactly.
    """
    notes: list[str] = []
    if opts.text_layer in ("native", "rebuild"):
        return opts.text_layer, notes

    for script in _scripts_for(opts.languages):
        if script == "latin":
            continue
        if _load_font(script) is None:
            notes.append(
                f"No embedded font available for {script} text, so pages are "
                f"rebuilt from the OCR render to keep the text layer accurate."
            )
            return "rebuild", notes
    return "native", notes


class _FontChain:
    """Picks, per word, the first loaded font that can draw every character."""

    def __init__(self, langs: Sequence[str]):
        self.entries = []
        scripts = list(_scripts_for(langs))
        scripts.sort(key=lambda s: 0 if s == "latin" else 1)
        if "latin" not in scripts:
            scripts.append("latin")
        for script in scripts:
            loaded = _load_font(script)
            if loaded:
                self.entries.append(loaded)
        if not self.entries:
            try:
                self.entries.append((pymupdf.Font("helv"), "helv", None))
            except Exception:
                pass

    def pick(self, text: str):
        for font, name, file in self.entries:
            try:
                if all(font.has_glyph(ord(ch)) for ch in text if not ch.isspace()):
                    return font, name, file
            except Exception:
                continue
        return self.entries[0] if self.entries else (None, "helv", None)

    @property
    def empty(self) -> bool:
        return not self.entries


# ---------------------------------------------------------------------------
# writing the invisible layer
# ---------------------------------------------------------------------------

def _write_invisible_layer(page, words: Sequence[OcrWord], zoom: float,
                           chain: _FontChain, offset: tuple[float, float] = (0.0, 0.0)) -> int:
    """
    Draw recognised words onto ``page`` in render mode 3 (invisible).

    Word boxes come from the rendered pixmap, so they are divided by ``zoom``
    to land back in PDF points. Font size is fitted to the measured box width
    so that selection rectangles line up with the visible ink.
    """
    if not words or chain.empty:
        return 0

    written = 0
    dx, dy = offset
    for w in words:
        text = w.text
        if not text.strip():
            continue
        font, fontname, fontfile = chain.pick(text)
        if font is None:
            continue

        x0 = w.x0 / zoom + dx
        y0 = w.y0 / zoom + dy
        x1 = w.x1 / zoom + dx
        y1 = w.y1 / zoom + dy
        box_w = max(0.6, x1 - x0)
        box_h = max(0.6, y1 - y0)

        # Fit the glyph run to the measured width; cap by height so a wide,
        # short box (common on noisy scans) cannot produce giant glyphs.
        try:
            unit = font.text_length(text, fontsize=1.0)
        except Exception:
            unit = 0.5 * len(text)
        size = (box_w / unit) if unit > 0 else box_h
        size = max(1.0, min(size, box_h * 1.6, 900.0))

        baseline = pymupdf.Point(x0, y1 - box_h * 0.18)
        try:
            page.insert_text(
                baseline, text,
                fontname=fontname,
                fontfile=fontfile,
                fontsize=size,
                render_mode=3,          # invisible: selectable + searchable, not drawn
                color=(0, 0, 0),
                overlay=True,
            )
            written += 1
        except Exception:
            try:
                page.insert_text(baseline, text, fontname="helv", fontsize=size,
                                 render_mode=3, overlay=True)
                written += 1
            except Exception:
                continue
    return written


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

def ocr_pdf(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    opts: OcrOptions | None = None,
    password: str | None = None,
) -> OcrResult:
    """
    OCR a PDF. Produces a searchable PDF, extracted text, or both, depending on
    ``opts.mode``.
    """
    exe = _require_tesseract()
    opts = opts or OcrOptions.from_settings()
    src = Path(source)
    result = OcrResult()

    have, missing = check_languages(opts.languages)
    if missing:
        result.warnings.append(
            "Language pack(s) not installed: "
            + ", ".join(deps.language_label(m) for m in missing)
        )
        if not have:
            raise JobError(
                "None of the selected OCR languages are installed.\n\n"
                "Install the language pack, or choose a different language."
            )
        opts = OcrOptions(**{**opts.__dict__, "languages": have})

    engine, notes = resolve_text_layer(opts)
    result.engine = engine
    result.warnings.extend(notes)

    want_pdf = opts.mode in ("searchable", "both")
    want_text = opts.mode in ("text", "both")

    out_pdf = None
    if want_pdf:
        out_pdf = utils.resolve_collision(
            Path(output) if output else utils.suggest_output(src, "ocr", ".pdf")
        )
        utils.ensure_parent(out_pdf)

    zoom = max(70, opts.dpi) / 72.0
    chain = _FontChain(opts.languages) if engine == "native" else _FontChain(["eng"])
    text_parts: list[str] = []

    tmpdir = Path(tempfile.mkdtemp(prefix="pmt_ocr_", dir=str(paths.temp_dir())))
    doc = open_pdf(src, password)
    try:
        total = doc.page_count
        result.page_count = total
        targets = utils.parse_page_ranges(opts.pages, total)
        if not targets:
            raise JobError("The selected page range contains no pages.")

        ctx.set_total(len(targets), "Recognising text")
        ctx.log(f"OCR engine: {engine} · languages: {opts.lang_arg} · {opts.dpi} DPI")

        for n, pno in enumerate(targets, start=1):
            ctx.check_cancel()
            page_res = OcrPageResult(index=pno)
            page = doc[pno - 1]

            if opts.skip_pages_with_text and page_has_text(page, 24):
                page_res.skipped = True
                page_res.reason = "already contains text"
                result.skipped_pages += 1
                result.pages.append(page_res)
                if want_text:
                    text_parts.append(page.get_text("text") or "")
                ctx.step(detail=f"Page {pno}: skipped (already searchable)")
                continue

            ctx.stage("Recognising text")

            # -- render ----------------------------------------------------
            original_rotation = page.rotation
            if engine == "native" and original_rotation:
                # Work in the unrotated system so pixel->point mapping is 1:1,
                # then restore the rotation so image and text rotate together.
                page.set_rotation(0)

            try:
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            except Exception as e:
                page_res.skipped = True
                page_res.reason = f"render failed: {e}"
                result.warnings.append(f"Page {pno}: could not be rendered ({e})")
                result.pages.append(page_res)
                if original_rotation:
                    page.set_rotation(original_rotation)
                ctx.step()
                continue

            img = _pixmap_to_pil(pix)
            img_path = tmpdir / f"page_{pno:05d}.png"

            # -- auto rotate ----------------------------------------------
            if opts.auto_rotate:
                img.save(img_path, "PNG")
                rot = detect_orientation(img_path)
                if rot:
                    img = img.rotate(-rot, expand=True, fillcolor="white")
                    page_res.rotation_applied = rot

            # -- preprocess -----------------------------------------------
            proc, skew = preprocess(img, opts)
            page_res.skew_corrected = skew
            proc.save(img_path, "PNG")

            # A rotated or deskewed image no longer maps 1:1 onto the page, so
            # the invisible layer would land in the wrong place. Rebuild the
            # page from the OCR render in that case to stay honest.
            geometry_changed = bool(page_res.rotation_applied) or abs(skew) >= 0.15
            page_engine = "rebuild" if (engine == "native" and geometry_changed and want_pdf) else engine

            # -- recognise -------------------------------------------------
            if page_engine == "rebuild" and want_pdf:
                base = str(tmpdir / f"out_{pno:05d}")
                r = _run_tesseract(_tess_cmd(exe, img_path, base, opts, "pdf"))
                if r.returncode != 0:
                    result.warnings.append(f"Page {pno}: OCR failed — {_tess_error(r)}")
                    page_res.skipped = True
                    page_res.reason = "tesseract error"
                else:
                    page_pdf = Path(base + ".pdf")
                    if page_pdf.is_file():
                        _replace_page_from_pdf(doc, pno - 1, page_pdf)
                if want_text or True:
                    words, txt = _recognise_words(exe, img_path, opts, tmpdir, pno)
                    page_res.words = words
                    page_res.text = txt
            else:
                words, txt = _recognise_words(exe, img_path, opts, tmpdir, pno)
                page_res.words = words
                page_res.text = txt
                if want_pdf and words:
                    _write_invisible_layer(page, words, zoom, chain)

            if original_rotation and engine == "native":
                try:
                    doc[pno - 1].set_rotation(original_rotation)
                except Exception:
                    pass

            if page_res.words:
                page_res.mean_confidence = sum(w.conf for w in page_res.words) / len(page_res.words)
            if page_res.text.strip():
                result.ocred_pages += 1
            text_parts.append(page_res.text)
            result.pages.append(page_res)

            try:
                img_path.unlink(missing_ok=True)
            except OSError:
                pass

            ctx.step(detail=f"Page {pno} · {len(page_res.words)} words")

        # -- write outputs --------------------------------------------------
        if want_pdf and out_pdf is not None:
            ctx.stage("Saving searchable PDF")
            ctx.progress(96, "Saving searchable PDF")
            doc.save(str(out_pdf), garbage=3, deflate=True)
            result.outputs.append(out_pdf)

        result.text = "\n\n".join(t for t in text_parts if t is not None)

        if want_text and opts.side_text_file:
            txt_path = utils.suggest_output(src, "ocr", ".txt")
            txt_path.write_text(result.text, encoding="utf-8")
            result.outputs.append(txt_path)

    finally:
        doc.close()
        shutil.rmtree(tmpdir, ignore_errors=True)

    result.message = (
        f"OCR complete — {result.ocred_pages} page(s) recognised"
        + (f", {result.skipped_pages} already searchable" if result.skipped_pages else "")
    )
    ctx.progress(100, result.message)
    return result


def _recognise_words(exe: str, img_path: Path, opts: OcrOptions,
                     tmpdir: Path, pno: int) -> tuple[list[OcrWord], str]:
    """Run Tesseract in TSV mode and return (words, plain text)."""
    base = str(tmpdir / f"tsv_{pno:05d}")
    r = _run_tesseract(_tess_cmd(exe, img_path, base, opts, "tsv"))
    tsv_file = Path(base + ".tsv")
    if r.returncode != 0 or not tsv_file.is_file():
        return [], ""
    try:
        tsv = tsv_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], ""
    finally:
        try:
            tsv_file.unlink(missing_ok=True)
        except OSError:
            pass

    words = _parse_tsv(tsv, opts.min_confidence)
    return words, _words_to_text(words)


def _words_to_text(words: Sequence[OcrWord]) -> str:
    """Reassemble words into lines and paragraphs using their layout indices."""
    if not words:
        return ""
    lines: dict[tuple[int, int], list[OcrWord]] = {}
    for w in words:
        lines.setdefault((w.block, w.line), []).append(w)

    ordered = sorted(lines.items(), key=lambda kv: (min(w.y0 for w in kv[1]),
                                                    min(w.x0 for w in kv[1])))
    out: list[str] = []
    prev_block = None
    for (block, _line), ws in ordered:
        if prev_block is not None and block != prev_block:
            out.append("")
        ws.sort(key=lambda w: w.x0)
        out.append(" ".join(w.text for w in ws))
        prev_block = block
    return "\n".join(out).strip()


def _replace_page_from_pdf(doc, index: int, page_pdf: Path) -> None:
    """Swap page ``index`` for the single page in ``page_pdf`` (Tesseract output)."""
    try:
        other = pymupdf.open(str(page_pdf))
    except Exception:
        return
    try:
        if other.page_count == 0:
            return
        doc.insert_pdf(other, from_page=0, to_page=0, start_at=index)
        doc.delete_page(index + 1)
    except Exception:
        pass
    finally:
        other.close()


def _tess_error(r: subprocess.CompletedProcess) -> str:
    msg = (r.stderr or r.stdout or "").strip().splitlines()
    return msg[-1] if msg else f"exit code {r.returncode}"


# ---------------------------------------------------------------------------
# image OCR
# ---------------------------------------------------------------------------

def ocr_images(
    ctx: JobContext,
    images: Sequence[str | Path],
    output: str | Path | None = None,
    opts: OcrOptions | None = None,
    make_pdf: bool = True,
) -> OcrResult:
    """
    OCR standalone image files.

    With ``make_pdf`` the images become one searchable PDF (each image on its
    own page at its native aspect ratio); otherwise only text is returned.
    """
    exe = _require_tesseract()
    opts = opts or OcrOptions.from_settings()
    files = [Path(p) for p in images]
    if not files:
        raise JobError("Add at least one image.")

    result = OcrResult(engine="images")
    tmpdir = Path(tempfile.mkdtemp(prefix="pmt_ocrimg_", dir=str(paths.temp_dir())))
    out_doc = pymupdf.open() if make_pdf else None
    chain = _FontChain(opts.languages)
    text_parts: list[str] = []

    try:
        ctx.set_total(len(files), "Recognising images")
        for i, f in enumerate(files, start=1):
            ctx.check_cancel()
            page_res = OcrPageResult(index=i)
            if Image is None:
                raise JobError("Pillow is required to process images.")
            try:
                img = Image.open(f)
                img.load()
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
            except Exception as e:
                result.warnings.append(f"{f.name}: could not be opened ({e})")
                ctx.step()
                continue

            proc, skew = preprocess(img, opts)
            page_res.skew_corrected = skew
            work = tmpdir / f"img_{i:05d}.png"
            proc.save(work, "PNG")

            words, txt = _recognise_words(exe, work, opts, tmpdir, i)
            page_res.words = words
            page_res.text = txt
            if words:
                page_res.mean_confidence = sum(w.conf for w in words) / len(words)
            text_parts.append(txt)

            if out_doc is not None:
                # Page in points at 72 dpi equivalent of the *original* image
                dpi_x = img.info.get("dpi", (opts.dpi, opts.dpi))[0] or opts.dpi
                scale = 72.0 / float(dpi_x or 72)
                pw, ph = img.width * scale, img.height * scale
                if pw < 10 or ph < 10:
                    pw, ph = 595.0, 842.0
                page = out_doc.new_page(width=pw, height=ph)
                rect = pymupdf.Rect(0, 0, pw, ph)
                try:
                    page.insert_image(rect, filename=str(f), keep_proportion=True)
                except Exception:
                    buf = tmpdir / f"conv_{i:05d}.png"
                    img.save(buf, "PNG")
                    page.insert_image(rect, filename=str(buf), keep_proportion=True)
                if words:
                    zoom_x = proc.width / pw
                    _write_invisible_layer(page, words, zoom_x, chain)

            result.pages.append(page_res)
            result.ocred_pages += 1 if txt.strip() else 0
            ctx.step(detail=f"{f.name} · {len(words)} words")

        result.text = "\n\n".join(text_parts)

        if out_doc is not None:
            if out_doc.page_count == 0:
                raise JobError("No images could be processed.")
            out = utils.resolve_collision(
                Path(output) if output else utils.suggest_output(files[0], "ocr", ".pdf")
            )
            utils.ensure_parent(out)
            ctx.stage("Saving searchable PDF")
            out_doc.save(str(out), garbage=3, deflate=True)
            result.outputs.append(out)
    finally:
        if out_doc is not None:
            out_doc.close()
        shutil.rmtree(tmpdir, ignore_errors=True)

    result.page_count = len(files)
    result.message = f"OCR complete — {len(files)} image(s)"
    ctx.progress(100, result.message)
    return result


# ---------------------------------------------------------------------------
# text-only convenience wrapper
# ---------------------------------------------------------------------------

def ocr_to_text(ctx: JobContext, source: str | Path, opts: OcrOptions | None = None,
                password: str | None = None) -> OcrResult:
    opts = opts or OcrOptions.from_settings()
    opts = OcrOptions(**{**opts.__dict__, "mode": "text"})
    return ocr_pdf(ctx, source, None, opts, password)


def quick_page_text(source: str | Path, page_index: int, opts: OcrOptions | None = None) -> str:
    """Synchronous single-page OCR used by the 'Run OCR?' prompt in Extract Text."""
    from app.core.jobs import NullContext
    opts = opts or OcrOptions.from_settings()
    opts = OcrOptions(**{**opts.__dict__, "mode": "text", "pages": str(page_index + 1)})
    res = ocr_pdf(NullContext(), source, None, opts)
    return res.text
