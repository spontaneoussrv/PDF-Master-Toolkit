"""
Workflow engine — the registry of chainable operations, and the runner that
executes a chain against one file.

Everything the Workflow Builder and Batch Processing screens can do is defined
in :data:`OPERATIONS`. Adding a new chainable tool means adding one entry here
and nothing else: both screens read this registry to build their UI, validate
parameters and execute steps.

A workflow runs each step on the *output of the previous step*, keeping all
intermediates in a scratch folder and copying only the final result to the
destination — so a failure halfway through never leaves a half-processed file
sitting where the user expects a finished one.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from app import paths
from app.core import (annotate, compress, convert, images, metadata, ocr,
                      pdf_ops, redaction, repair, security, textops, utils)
from app.core.jobs import Cancelled, JobContext, JobError


# ---------------------------------------------------------------------------
# parameter descriptors (drive the auto-generated step editors)
# ---------------------------------------------------------------------------

@dataclass
class Param:
    key: str
    label: str
    kind: str                       # text | int | float | bool | choice | color | path | pages
    default: Any = None
    choices: list[tuple[str, str]] = field(default_factory=list)   # (value, label)
    minimum: float = 0
    maximum: float = 1000
    hint: str = ""
    optional: bool = True


@dataclass
class Operation:
    key: str
    label: str
    category: str
    description: str
    run: Callable[..., Any]
    params: list[Param] = field(default_factory=list)
    produces: str = "pdf"           # pdf | files | text | none
    accepts: tuple[str, ...] = (".pdf",)
    icon: str = "•"

    def defaults(self) -> dict:
        return {p.key: p.default for p in self.params}


# ---------------------------------------------------------------------------
# step implementations
# ---------------------------------------------------------------------------

def _op_ocr(ctx, src: Path, out: Path, p: dict) -> Path:
    opts = ocr.OcrOptions.from_settings()
    langs = p.get("languages") or "eng"
    opts.languages = [l.strip() for l in str(langs).replace("+", ",").split(",") if l.strip()]
    opts.dpi = int(p.get("dpi", 300))
    opts.mode = "searchable"
    opts.deskew = bool(p.get("deskew", True))
    opts.auto_rotate = bool(p.get("auto_rotate", True))
    opts.skip_pages_with_text = bool(p.get("skip_text", True))
    res = ocr.ocr_pdf(ctx, src, out, opts)
    if not res.output:
        raise JobError("OCR produced no output.")
    return res.output


def _op_compress(ctx, src: Path, out: Path, p: dict) -> Path:
    opts = compress.CompressOptions.from_profile(p.get("profile", "balanced"))
    if p.get("profile") == "custom":
        opts.dpi = int(p.get("dpi", 150))
        opts.jpeg_quality = int(p.get("quality", 72))
    opts.grayscale_images = bool(p.get("grayscale", False))
    res = compress.compress(ctx, src, out, opts)
    return res.output or out


def _op_watermark(ctx, src: Path, out: Path, p: dict) -> Path:
    opts = annotate.WatermarkOptions(
        kind="image" if p.get("image_path") else "text",
        text=str(p.get("text", "CONFIDENTIAL")),
        image_path=Path(p["image_path"]) if p.get("image_path") else None,
        color=str(p.get("color", "#FF0000")),
        opacity=float(p.get("opacity", 0.18)),
        rotation=float(p.get("rotation", 45)),
        position=str(p.get("position", "center")),
        tile=bool(p.get("tile", False)),
        behind=bool(p.get("behind", True)),
        pages=str(p.get("pages", "all")),
    )
    return annotate.watermark(ctx, src, out, opts).output or out


def _op_page_numbers(ctx, src: Path, out: Path, p: dict) -> Path:
    opts = annotate.PageNumberOptions(
        fmt=str(p.get("format", "page_n_of_m")),
        position=str(p.get("position", "bottom-center")),
        font_size=float(p.get("size", 10)),
        start_number=int(p.get("start", 1)),
        skip_first=bool(p.get("skip_first", False)),
        pages=str(p.get("pages", "all")),
    )
    return annotate.page_numbers(ctx, src, out, opts).output or out


def _op_header_footer(ctx, src: Path, out: Path, p: dict) -> Path:
    opts = annotate.HeaderFooterOptions(
        header_left=str(p.get("header_left", "")),
        header_center=str(p.get("header_center", "")),
        header_right=str(p.get("header_right", "")),
        footer_left=str(p.get("footer_left", "")),
        footer_center=str(p.get("footer_center", "")),
        footer_right=str(p.get("footer_right", "")),
        font_size=float(p.get("size", 9)),
        pages=str(p.get("pages", "all")),
    )
    return annotate.header_footer(ctx, src, out, opts).output or out


def _op_rotate(ctx, src: Path, out: Path, p: dict) -> Path:
    return pdf_ops.rotate(ctx, src, int(p.get("angle", 90)), out,
                          str(p.get("pages", "all"))).output or out


def _op_protect(ctx, src: Path, out: Path, p: dict) -> Path:
    opts = security.ProtectOptions(
        user_password=str(p.get("open_password", "")),
        owner_password=str(p.get("owner_password", "")),
        method=str(p.get("method", "aes256")),
        allow={
            "print": bool(p.get("allow_print", True)),
            "print_hq": bool(p.get("allow_print", True)),
            "copy": bool(p.get("allow_copy", False)),
            "modify": bool(p.get("allow_modify", False)),
            "annotate": bool(p.get("allow_annotate", False)),
            "form": True, "assemble": False, "accessibility": True,
        },
    )
    return security.protect(ctx, src, out, opts).output or out


def _op_unlock(ctx, src: Path, out: Path, p: dict) -> Path:
    return security.unlock(ctx, src, str(p.get("password", "")), out).output or out


def _op_remove_metadata(ctx, src: Path, out: Path, p: dict) -> Path:
    return metadata.remove_metadata(
        ctx, src, out,
        clear_xmp=True,
        clear_bookmarks=bool(p.get("clear_bookmarks", False)),
        clear_attachments=bool(p.get("clear_attachments", False)),
    )


def _op_set_metadata(ctx, src: Path, out: Path, p: dict) -> Path:
    values = {k: str(p.get(k, "")) for k in ("title", "author", "subject", "keywords")
              if p.get(k)}
    if not values:
        raise JobError("No metadata values were provided for this step.")
    return metadata.write_metadata(ctx, src, values, out)


def _op_normalize(ctx, src: Path, out: Path, p: dict) -> Path:
    return pdf_ops.normalize_page_size(
        ctx, src, out,
        page_size=str(p.get("page_size", "A4")),
        orientation=str(p.get("orientation", "auto")),
        mode=str(p.get("mode", "fit")),
        margin=float(p.get("margin", 0)),
    ).output or out


def _op_extract_pages(ctx, src: Path, out: Path, p: dict) -> Path:
    return pdf_ops.extract_pages(ctx, src, str(p.get("pages", "all")), out).output or out


def _op_remove_pages(ctx, src: Path, out: Path, p: dict) -> Path:
    return pdf_ops.remove_pages(ctx, src, str(p.get("pages", "")), out).output or out


def _op_crop(ctx, src: Path, out: Path, p: dict) -> Path:
    if p.get("auto", True):
        return pdf_ops.auto_crop_margins(ctx, src, out,
                                         padding=float(p.get("padding", 6))).output or out
    m = (float(p.get("left", 0)), float(p.get("top", 0)),
         float(p.get("right", 0)), float(p.get("bottom", 0)))
    return pdf_ops.crop(ctx, src, out, margins=m,
                        pages_spec=str(p.get("pages", "all"))).output or out


def _op_redact(ctx, src: Path, out: Path, p: dict) -> Path:
    terms = [t.strip() for t in str(p.get("terms", "")).replace("\n", ",").split(",") if t.strip()]
    if not terms:
        raise JobError("Enter at least one term to redact.")
    opts = redaction.RedactOptions(make_backup=False,
                                   case_sensitive=bool(p.get("case_sensitive", False)))
    return redaction.redact_terms(ctx, src, terms, out, opts,
                                  regex=bool(p.get("regex", False))).output or out


def _op_repair(ctx, src: Path, out: Path, p: dict) -> Path:
    return repair.repair(ctx, src, out, deep=bool(p.get("deep", True))).output or out


def _op_rewrite(ctx, src: Path, out: Path, p: dict) -> Path:
    return pdf_ops.rewrite(ctx, src, out,
                           linearize=bool(p.get("linearize", False))).output or out


def _op_to_images(ctx, src: Path, out: Path, p: dict):
    opts = images.RasterOptions(fmt=str(p.get("format", "png")),
                                dpi=int(p.get("dpi", 150)),
                                quality=int(p.get("quality", 90)))
    res = images.pdf_to_images(ctx, src, out.parent / f"{src.stem}_images", opts)
    return res.outputs


def _op_to_word(ctx, src: Path, out: Path, p: dict) -> Path:
    target = out.with_suffix(".docx")
    res = convert.pdf_to_word(ctx, src, target)
    return res.output or target


def _op_to_excel(ctx, src: Path, out: Path, p: dict) -> Path:
    target = out.with_suffix(".xlsx" if p.get("format", "xlsx") == "xlsx" else ".csv")
    opts = convert.PdfToExcelOptions(fmt=str(p.get("format", "xlsx")))
    res = convert.pdf_to_excel(ctx, src, target, opts)
    return res.output or target


def _op_extract_text(ctx, src: Path, out: Path, p: dict) -> Path:
    fmt = str(p.get("format", "txt"))
    res = textops.extract_text(ctx, src)
    target = out.with_suffix("." + fmt)
    return textops.export_text(ctx, res, target, fmt, src.name)


def _op_extract_images(ctx, src: Path, out: Path, p: dict):
    opts = images.ExtractImagesOptions(fmt=str(p.get("format", "original")),
                                       min_width=int(p.get("min_size", 64)),
                                       min_height=int(p.get("min_size", 64)))
    return images.extract_images(ctx, src, out.parent / f"{src.stem}_images", opts).outputs


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

PAGE_PARAM = Param("pages", "Pages", "pages", "all",
                   hint="all, 1-5, 1,3,7-9, odd, even")

POSITION_CHOICES = [(p, p.replace("-", " ").title()) for p in annotate.POSITIONS]

OPERATIONS: dict[str, Operation] = {}


def _register(op: Operation) -> None:
    OPERATIONS[op.key] = op


_register(Operation(
    "ocr", "OCR — make searchable", "Recognition",
    "Recognise text in scanned pages and add an invisible searchable layer.",
    _op_ocr, icon="◍",
    params=[
        Param("languages", "Languages", "text", "eng", hint="eng, or eng+ben for two"),
        Param("dpi", "Resolution (DPI)", "int", 300, minimum=100, maximum=600),
        Param("deskew", "Straighten crooked scans", "bool", True),
        Param("auto_rotate", "Correct page orientation", "bool", True),
        Param("skip_text", "Skip pages that already have text", "bool", True),
    ],
))

_register(Operation(
    "compress", "Compress", "Optimise",
    "Reduce file size by downsampling images and cleaning the file structure.",
    _op_compress, icon="⤓",
    params=[
        Param("profile", "Profile", "choice", "balanced",
              choices=[(k, compress.PROFILES[k].label) for k in compress.PROFILE_ORDER]),
        Param("dpi", "Image DPI (custom only)", "int", 150, minimum=50, maximum=600),
        Param("quality", "JPEG quality (custom only)", "int", 72, minimum=20, maximum=95),
        Param("grayscale", "Convert images to grayscale", "bool", False),
    ],
))

_register(Operation(
    "watermark", "Add watermark", "Stamp",
    "Overlay text or an image across the pages.",
    _op_watermark, icon="◈",
    params=[
        Param("text", "Watermark text", "text", "CONFIDENTIAL"),
        Param("color", "Colour", "color", "#FF0000"),
        Param("opacity", "Opacity", "float", 0.18, minimum=0.02, maximum=1.0),
        Param("rotation", "Rotation (degrees)", "float", 45, minimum=-180, maximum=180),
        Param("position", "Position", "choice", "center", choices=POSITION_CHOICES),
        Param("tile", "Repeat across the page", "bool", False),
        Param("behind", "Place behind page content", "bool", True),
        PAGE_PARAM,
    ],
))

_register(Operation(
    "page_numbers", "Add page numbers", "Stamp",
    "Number the pages in one of several formats.",
    _op_page_numbers, icon="№",
    params=[
        Param("format", "Format", "choice", "page_n_of_m",
              choices=[(k, annotate.NUMBER_FORMATS[k][0]) for k in annotate.NUMBER_FORMAT_ORDER]),
        Param("position", "Position", "choice", "bottom-center", choices=POSITION_CHOICES),
        Param("size", "Font size", "float", 10, minimum=5, maximum=36),
        Param("start", "Start at", "int", 1, minimum=1, maximum=100000),
        Param("skip_first", "Skip the first page", "bool", False),
        PAGE_PARAM,
    ],
))

_register(Operation(
    "header_footer", "Add header / footer", "Stamp",
    "Add running headers and footers with variables such as {page} and {total}.",
    _op_header_footer, icon="▤",
    params=[
        Param("header_left", "Header left", "text", ""),
        Param("header_center", "Header centre", "text", ""),
        Param("header_right", "Header right", "text", ""),
        Param("footer_left", "Footer left", "text", ""),
        Param("footer_center", "Footer centre", "text", "Page {page} of {total}"),
        Param("footer_right", "Footer right", "text", ""),
        Param("size", "Font size", "float", 9, minimum=5, maximum=24),
        PAGE_PARAM,
    ],
))

_register(Operation(
    "rotate", "Rotate pages", "Pages",
    "Turn pages by 90, 180 or 270 degrees.",
    _op_rotate, icon="↻",
    params=[
        Param("angle", "Angle", "choice", 90,
              choices=[(90, "90° clockwise"), (180, "180°"), (270, "270° (90° anticlockwise)")]),
        PAGE_PARAM,
    ],
))

_register(Operation(
    "extract_pages", "Keep only these pages", "Pages",
    "Build a new document from the selected pages.",
    _op_extract_pages, icon="⬒",
    params=[Param("pages", "Pages to keep", "pages", "1-5", optional=False)],
))

_register(Operation(
    "remove_pages", "Delete pages", "Pages",
    "Remove the selected pages from the document.",
    _op_remove_pages, icon="⌦",
    params=[Param("pages", "Pages to delete", "pages", "", optional=False)],
))

_register(Operation(
    "crop", "Crop pages", "Pages",
    "Trim margins, either automatically or by a fixed amount.",
    _op_crop, icon="⛶",
    params=[
        Param("auto", "Detect content and trim white space", "bool", True),
        Param("padding", "Padding to keep (points)", "float", 6, minimum=0, maximum=120),
        Param("left", "Left margin (points)", "float", 0, minimum=0, maximum=400),
        Param("top", "Top margin (points)", "float", 0, minimum=0, maximum=400),
        Param("right", "Right margin (points)", "float", 0, minimum=0, maximum=400),
        Param("bottom", "Bottom margin (points)", "float", 0, minimum=0, maximum=400),
        PAGE_PARAM,
    ],
))

_register(Operation(
    "normalize", "Resize to a standard page", "Pages",
    "Scale every page onto a uniform sheet size such as A4 or Letter.",
    _op_normalize, icon="▭",
    params=[
        Param("page_size", "Page size", "choice", "A4",
              choices=[(k, k) for k in ("A3", "A4", "A5", "Letter", "Legal", "Tabloid")]),
        Param("orientation", "Orientation", "choice", "auto",
              choices=[("auto", "Match each page"), ("portrait", "Portrait"),
                       ("landscape", "Landscape"), ("match", "Match the first page")]),
        Param("mode", "Scaling", "choice", "fit",
              choices=[("fit", "Fit inside (no cropping)"), ("fill", "Fill (crop overflow)"),
                       ("stretch", "Stretch to fill")]),
        Param("margin", "Margin (points)", "float", 0, minimum=0, maximum=144),
    ],
))

_register(Operation(
    "redact", "Redact terms", "Security",
    "Permanently remove every occurrence of the given words or phrases.",
    _op_redact, icon="▪",
    params=[
        Param("terms", "Words or phrases", "text", "", optional=False,
              hint="Separate with commas"),
        Param("case_sensitive", "Match case", "bool", False),
        Param("regex", "Treat as a regular expression", "bool", False),
    ],
))

_register(Operation(
    "protect", "Password protect", "Security",
    "Encrypt the document and set permission restrictions.",
    _op_protect, icon="🔒",
    params=[
        Param("open_password", "Open password", "text", ""),
        Param("owner_password", "Permissions password", "text", ""),
        Param("method", "Encryption", "choice", "aes256",
              choices=[("aes256", "AES 256-bit"), ("aes128", "AES 128-bit"),
                       ("rc4_128", "RC4 128-bit (legacy)")]),
        Param("allow_print", "Allow printing", "bool", True),
        Param("allow_copy", "Allow copying text", "bool", False),
        Param("allow_modify", "Allow editing", "bool", False),
        Param("allow_annotate", "Allow annotations", "bool", False),
    ],
))

_register(Operation(
    "unlock", "Remove password", "Security",
    "Decrypt a document using the password you provide.",
    _op_unlock, icon="🔓",
    params=[Param("password", "Current password", "text", "", optional=False)],
))

_register(Operation(
    "remove_metadata", "Remove metadata", "Security",
    "Clear title, author and other document properties.",
    _op_remove_metadata, icon="⊘",
    params=[
        Param("clear_bookmarks", "Also remove bookmarks", "bool", False),
        Param("clear_attachments", "Also remove attachments", "bool", False),
    ],
))

_register(Operation(
    "set_metadata", "Set metadata", "Security",
    "Write document properties such as title and author.",
    _op_set_metadata, icon="✎",
    params=[
        Param("title", "Title", "text", ""),
        Param("author", "Author", "text", ""),
        Param("subject", "Subject", "text", ""),
        Param("keywords", "Keywords", "text", ""),
    ],
))

_register(Operation(
    "repair", "Repair", "Optimise",
    "Attempt to recover a damaged document.",
    _op_repair, icon="⚕",
    params=[Param("deep", "Try deep reconstruction if needed", "bool", True)],
))

_register(Operation(
    "rewrite", "Clean and optimise structure", "Optimise",
    "Rewrite the file, dropping unused objects and compressing streams.",
    _op_rewrite, icon="✧",
    params=[Param("linearize", "Optimise for fast web view", "bool", False)],
))

_register(Operation(
    "to_images", "Export pages as images", "Export",
    "Render each page to an image file.",
    _op_to_images, produces="files", icon="🖼",
    params=[
        Param("format", "Format", "choice", "png",
              choices=[(k, v["label"]) for k, v in images.IMAGE_FORMATS.items()]),
        Param("dpi", "Resolution (DPI)", "int", 150, minimum=48, maximum=600),
        Param("quality", "Quality", "int", 90, minimum=20, maximum=100),
    ],
))

_register(Operation(
    "to_word", "Convert to Word", "Export",
    "Produce an editable .docx from the document.",
    _op_to_word, produces="files", icon="W",
    params=[],
))

_register(Operation(
    "to_excel", "Extract tables to Excel", "Export",
    "Detect tables and export them to a workbook.",
    _op_to_excel, produces="files", icon="X",
    params=[Param("format", "Format", "choice", "xlsx",
                  choices=[("xlsx", "Excel workbook"), ("csv", "CSV")])],
))

_register(Operation(
    "extract_text", "Extract text", "Export",
    "Save the document's text to a file.",
    _op_extract_text, produces="files", icon="T",
    params=[Param("format", "Format", "choice", "txt",
                  choices=[(k, v) for k, v in textops.EXPORT_FORMATS.items()])],
))

_register(Operation(
    "extract_images", "Extract embedded images", "Export",
    "Pull the pictures out of the document.",
    _op_extract_images, produces="files", icon="⧉",
    params=[
        Param("format", "Save as", "choice", "original",
              choices=[("original", "Original format"), ("png", "PNG"), ("jpg", "JPEG")]),
        Param("min_size", "Minimum size (pixels)", "int", 64, minimum=1, maximum=4000),
    ],
))


CATEGORY_ORDER = ["Recognition", "Optimise", "Pages", "Stamp", "Security", "Export"]


def operations_by_category() -> dict[str, list[Operation]]:
    out: dict[str, list[Operation]] = {c: [] for c in CATEGORY_ORDER}
    for op in OPERATIONS.values():
        out.setdefault(op.category, []).append(op)
    for ops in out.values():
        ops.sort(key=lambda o: o.label)
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------------------
# steps + runner
# ---------------------------------------------------------------------------

@dataclass
class Step:
    op: str
    params: dict = field(default_factory=dict)
    enabled: bool = True

    @property
    def operation(self) -> Operation | None:
        return OPERATIONS.get(self.op)

    @property
    def label(self) -> str:
        o = self.operation
        return o.label if o else self.op

    def to_dict(self) -> dict:
        return {"op": self.op, "params": dict(self.params), "enabled": self.enabled}

    @classmethod
    def from_dict(cls, data: dict) -> "Step":
        return cls(str(data.get("op", "")), dict(data.get("params") or {}),
                   bool(data.get("enabled", True)))


@dataclass
class WorkflowRunResult:
    source: Path
    outputs: list[Path] = field(default_factory=list)
    steps_run: int = 0
    warnings: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def output(self) -> Path | None:
        return self.outputs[0] if self.outputs else None


def validate(steps: Sequence[Step]) -> list[str]:
    """Static checks surfaced in the builder before the user hits Run."""
    problems: list[str] = []
    active = [s for s in steps if s.enabled]
    if not active:
        problems.append("The workflow has no enabled steps.")

    for i, step in enumerate(active, start=1):
        op = step.operation
        if op is None:
            problems.append(f"Step {i}: unknown operation '{step.op}'.")
            continue
        for p in op.params:
            if p.optional:
                continue
            value = step.params.get(p.key, p.default)
            if value in (None, "", []):
                problems.append(f"Step {i} ({op.label}): '{p.label}' is required.")

        if op.produces == "files" and i < len(active):
            problems.append(
                f"Step {i} ({op.label}) produces files rather than a PDF, so no "
                f"further step can run after it. Move it to the end."
            )
    return problems


def run_workflow(
    ctx: JobContext,
    source: str | Path,
    steps: Sequence[Step],
    out_dir: str | Path | None = None,
    final_name: str | None = None,
    password: str | None = None,
) -> WorkflowRunResult:
    """Execute the chain on one file, keeping intermediates in a scratch folder."""
    src = Path(source)
    active = [s for s in steps if s.enabled and s.operation is not None]
    if not active:
        raise JobError("This workflow has no steps to run.")

    result = WorkflowRunResult(source=src)
    scratch = Path(tempfile.mkdtemp(prefix="pmt_wf_", dir=str(paths.temp_dir())))
    current = src
    produced_files: list[Path] = []

    try:
        for index, step in enumerate(active, start=1):
            ctx.check_cancel()
            op = step.operation
            assert op is not None
            ctx.stage(f"Step {index}/{len(active)}: {op.label}")
            ctx.log(f"Step {index}: {op.label}")

            params = {**op.defaults(), **step.params}
            target = scratch / f"s{index:02d}_{utils.sanitize_filename(src.stem)}.pdf"

            try:
                produced = op.run(ctx, current, target, params)
            except Cancelled:
                raise
            except JobError:
                raise
            except Exception as e:
                raise JobError(f"Step {index} ({op.label}) failed: {e}") from e

            if op.produces == "files":
                items = produced if isinstance(produced, (list, tuple)) else [produced]
                produced_files.extend(Path(p) for p in items if p)
                result.steps_run += 1
                continue

            nxt = Path(produced) if produced else target
            if not nxt.exists():
                raise JobError(f"Step {index} ({op.label}) produced no output file.")
            current = nxt
            result.steps_run += 1

        # ---- deliver -----------------------------------------------------
        folder = Path(out_dir) if out_dir else src.parent
        folder.mkdir(parents=True, exist_ok=True)

        if current != src:
            stem = final_name or f"{src.stem}_processed"
            final = utils.unique_path(folder / f"{utils.sanitize_filename(stem)}.pdf")
            shutil.copy2(current, final)
            result.outputs.append(final)

        for f in produced_files:
            if not f.exists():
                continue
            try:
                if f.parent.resolve() == folder.resolve():
                    result.outputs.append(f)
                    continue
                dest = utils.unique_path(folder / f.name)
                shutil.copy2(f, dest)
                result.outputs.append(dest)
            except OSError as e:
                result.warnings.append(f"Could not save {f.name}: {e}")

        if not result.outputs:
            raise JobError("The workflow finished but produced no output.")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    result.message = f"{result.steps_run} step(s) completed · {len(result.outputs)} file(s) written"
    ctx.progress(100, result.message)
    return result


# ---------------------------------------------------------------------------
# presets
# ---------------------------------------------------------------------------

PRESET_WORKFLOWS: dict[str, list[Step]] = {
    "Scan clean-up": [
        Step("ocr", {"languages": "eng", "dpi": 300, "deskew": True, "auto_rotate": True}),
        Step("compress", {"profile": "balanced"}),
        Step("page_numbers", {"format": "page_n_of_m", "position": "bottom-center"}),
    ],
    "Prepare for email": [
        Step("compress", {"profile": "high"}),
        Step("remove_metadata", {}),
    ],
    "Confidential release": [
        Step("watermark", {"text": "CONFIDENTIAL", "opacity": 0.15, "tile": False}),
        Step("header_footer", {"footer_center": "Page {page} of {total}",
                               "header_right": "{date}"}),
        Step("protect", {"owner_password": "", "allow_print": True, "allow_copy": False}),
    ],
    "Archive scan": [
        Step("repair", {"deep": True}),
        Step("ocr", {"languages": "eng", "dpi": 300}),
        Step("normalize", {"page_size": "A4", "orientation": "auto"}),
        Step("compress", {"profile": "balanced"}),
        Step("set_metadata", {"title": "", "author": ""}),
    ],
}


def preset_steps(name: str) -> list[Step]:
    return [Step(s.op, dict(s.params), s.enabled) for s in PRESET_WORKFLOWS.get(name, [])]
