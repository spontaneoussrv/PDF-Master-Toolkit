"""
Thin, defensive wrapper around PyMuPDF.

Everything that touches a PDF goes through here so that:
  * the deprecated ``fitz`` alias and the modern ``pymupdf`` name both work,
  * encrypted documents get a single, consistent unlock path,
  * damaged documents raise one recognisable exception type,
  * nothing else in the codebase has to know which backend is in use.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

try:                                    # PyMuPDF >= 1.24 prefers this name
    import pymupdf                      # type: ignore
except ImportError:                     # pragma: no cover - older builds
    import fitz as pymupdf              # type: ignore

fitz = pymupdf                          # legacy alias used across the codebase

# MuPDF writes low-level parse diagnostics straight to stderr ("aes padding out
# of range", "syntax error: ..."). They are noise in a GUI app -- every failure
# that matters is already surfaced as a typed exception below -- and in a frozen
# build with no console they can stall on a closed stream.
try:
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
except Exception:                       # pragma: no cover - older builds
    pass

__all__ = [
    "pymupdf", "fitz", "PdfLocked", "PdfDamaged", "PdfOpenError",
    "open_pdf", "document", "PageSize", "PAGE_SIZES", "page_size_points",
    "PERMISSION_FLAGS", "describe_permissions", "needs_password",
    "page_label", "safe_page_count", "rect_from_box",
]


# ---------------------------------------------------------------------------
# exceptions
# ---------------------------------------------------------------------------

class PdfOpenError(Exception):
    """Base for anything that stops a document from opening."""


class PdfLocked(PdfOpenError):
    """The document needs a password we do not have (or the one given is wrong)."""


class PdfDamaged(PdfOpenError):
    """The file is not a usable PDF; Repair PDF may help."""


# ---------------------------------------------------------------------------
# opening
# ---------------------------------------------------------------------------

def needs_password(path: str | Path) -> bool:
    try:
        doc = pymupdf.open(str(path))
    except Exception:
        return False
    try:
        return bool(doc.needs_pass)
    finally:
        doc.close()


#: Attribute stamped on every document returned by :func:`open_pdf`, recording
#: whether the file was encrypted *before* we authenticated. Read this instead
#: of ``doc.needs_pass`` — see :func:`was_encrypted`.
_ENCRYPTED_FLAG = "_pmt_was_encrypted"


def was_encrypted(doc) -> bool:
    """
    Whether the document was password protected when it was opened.

    ALWAYS use this instead of reading ``doc.needs_pass`` on an already-opened
    document.

    Reading ``needs_pass`` *after* a successful ``authenticate()`` re-runs
    MuPDF's password check and destroys the live AES decryption state. The
    document then silently yields empty content streams, and anything saved
    from it comes out blank — MuPDF only hints at this with an
    "aes padding out of range" message on stderr, which is suppressed above.
    That cost us a silently emptied file in Unlock PDF; do not reintroduce it.
    """
    return bool(getattr(doc, _ENCRYPTED_FLAG, False))


def open_pdf(path: str | Path, password: str | None = None, repair: bool = False):
    """
    Open a PDF, raising :class:`PdfLocked` / :class:`PdfDamaged` instead of the
    library's assorted RuntimeErrors. Caller owns closing the document.

    The returned document carries a flag recording whether it was encrypted, so
    callers never have to touch ``needs_pass`` afterwards (see
    :func:`was_encrypted`).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    if p.stat().st_size == 0:
        raise PdfDamaged(f"'{p.name}' is empty (0 bytes).")

    try:
        if repair:
            data = p.read_bytes()
            doc = pymupdf.open(stream=data, filetype="pdf")
        else:
            doc = pymupdf.open(str(p))
    except Exception as e:
        raise PdfDamaged(
            f"'{p.name}' could not be opened as a PDF. It may be damaged or "
            f"incomplete. ({e})"
        ) from e

    # Read needs_pass exactly once, before authenticating, and remember it.
    locked = bool(doc.needs_pass)
    try:
        setattr(doc, _ENCRYPTED_FLAG, locked)
    except Exception:                    # pragma: no cover - defensive
        pass

    if locked:
        ok = False
        if password is not None:
            try:
                ok = bool(doc.authenticate(password))
            except Exception:
                ok = False
        if not ok:
            doc.close()
            if password:
                raise PdfLocked(f"The password for '{p.name}' is incorrect.")
            raise PdfLocked(f"'{p.name}' is password protected.")
    return doc


@contextlib.contextmanager
def document(path: str | Path, password: str | None = None, repair: bool = False) -> Iterator:
    """Context-managed :func:`open_pdf`."""
    doc = open_pdf(path, password, repair)
    try:
        yield doc
    finally:
        with contextlib.suppress(Exception):
            doc.close()


def safe_page_count(path: str | Path, password: str | None = None) -> int:
    """Page count, or 0 when the file cannot be read. Never raises."""
    try:
        with document(path, password) as doc:
            return doc.page_count
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# page geometry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PageSize:
    name: str
    width: float          # points, portrait
    height: float

    def portrait(self) -> tuple[float, float]:
        return (self.width, self.height)

    def landscape(self) -> tuple[float, float]:
        return (self.height, self.width)

    def for_orientation(self, landscape: bool) -> tuple[float, float]:
        return self.landscape() if landscape else self.portrait()


PAGE_SIZES: dict[str, PageSize] = {
    "A0":       PageSize("A0", 2384, 3370),
    "A1":       PageSize("A1", 1684, 2384),
    "A2":       PageSize("A2", 1191, 1684),
    "A3":       PageSize("A3", 842, 1191),
    "A4":       PageSize("A4", 595, 842),
    "A5":       PageSize("A5", 420, 595),
    "A6":       PageSize("A6", 298, 420),
    "B4":       PageSize("B4", 729, 1032),
    "B5":       PageSize("B5", 516, 729),
    "Letter":   PageSize("Letter", 612, 792),
    "Legal":    PageSize("Legal", 612, 1008),
    "Tabloid":  PageSize("Tabloid", 792, 1224),
    "Executive": PageSize("Executive", 522, 756),
}


def page_size_points(name: str, landscape: bool = False) -> tuple[float, float]:
    ps = PAGE_SIZES.get(name, PAGE_SIZES["A4"])
    return ps.for_orientation(landscape)


def rect_from_box(box, page_rect) -> "pymupdf.Rect":
    """Clamp a rectangle to a page, tolerating inverted or oversized input."""
    x0, y0, x1, y1 = box
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    return pymupdf.Rect(
        max(page_rect.x0, x0),
        max(page_rect.y0, y0),
        min(page_rect.x1, x1),
        min(page_rect.y1, y1),
    )


def page_label(index: int, total: int) -> str:
    return f"Page {index + 1} of {total}"


# ---------------------------------------------------------------------------
# permissions
# ---------------------------------------------------------------------------

PERMISSION_FLAGS = {
    "print":         getattr(pymupdf, "PDF_PERM_PRINT", 1 << 2),
    "modify":        getattr(pymupdf, "PDF_PERM_MODIFY", 1 << 3),
    "copy":          getattr(pymupdf, "PDF_PERM_COPY", 1 << 4),
    "annotate":      getattr(pymupdf, "PDF_PERM_ANNOTATE", 1 << 5),
    "form":          getattr(pymupdf, "PDF_PERM_FORM", 1 << 8),
    "accessibility": getattr(pymupdf, "PDF_PERM_ACCESSIBILITY", 1 << 9),
    "assemble":      getattr(pymupdf, "PDF_PERM_ASSEMBLE", 1 << 10),
    "print_hq":      getattr(pymupdf, "PDF_PERM_PRINT_HQ", 1 << 11),
}

PERMISSION_LABELS = {
    "print": "Printing",
    "modify": "Editing content",
    "copy": "Copying text and images",
    "annotate": "Adding annotations",
    "form": "Filling forms",
    "accessibility": "Screen-reader access",
    "assemble": "Assembling pages",
    "print_hq": "High-resolution printing",
}


def describe_permissions(doc) -> dict[str, bool]:
    """Which actions the document's permission flags currently allow."""
    perms = getattr(doc, "permissions", None)
    if perms is None:
        return {k: True for k in PERMISSION_FLAGS}
    return {k: bool(perms & flag) for k, flag in PERMISSION_FLAGS.items()}


def build_permissions(allow: dict[str, bool]) -> int:
    value = 0
    for key, flag in PERMISSION_FLAGS.items():
        if allow.get(key, True):
            value |= flag
    return value


# ---------------------------------------------------------------------------
# text / content probes
# ---------------------------------------------------------------------------

def page_has_text(page, min_chars: int = 12) -> bool:
    try:
        return len((page.get_text("text") or "").strip()) >= min_chars
    except Exception:
        return False


def document_text_ratio(doc, sample: int = 12) -> float:
    """
    Fraction of sampled pages carrying real embedded text.

    Used to decide whether to suggest OCR. Sampling keeps this fast on a
    2000-page document.
    """
    total = doc.page_count
    if total == 0:
        return 0.0
    step = max(1, total // sample)
    indices = list(range(0, total, step))[:sample]
    hits = sum(1 for i in indices if page_has_text(doc[i]))
    return hits / len(indices)


def looks_scanned(doc) -> bool:
    return document_text_ratio(doc) < 0.15


def page_image_count(page) -> int:
    try:
        return len(page.get_images(full=True))
    except Exception:
        return 0


def new_document():
    return pymupdf.open()


def version() -> str:
    return getattr(pymupdf, "__doc__", "PyMuPDF").split(":")[0].strip()
