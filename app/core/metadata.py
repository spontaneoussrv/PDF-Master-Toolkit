"""Read, edit and strip PDF metadata."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from app.core import utils
from app.core.jobs import JobContext, JobError
from app.core.pdfbase import (document, describe_permissions, pymupdf,
                              was_encrypted)

FIELDS = ["title", "author", "subject", "keywords", "creator", "producer",
          "creationDate", "modDate"]

FIELD_LABELS = {
    "title": "Title",
    "author": "Author",
    "subject": "Subject",
    "keywords": "Keywords",
    "creator": "Creator (authoring application)",
    "producer": "Producer (PDF writer)",
    "creationDate": "Created",
    "modDate": "Modified",
}

METADATA_DISCLAIMER = (
    "Removing metadata clears the document information dictionary and the XMP "
    "packet. It does not guarantee anonymity: page content, embedded fonts, "
    "image EXIF data, revision history in the file and any text inside the "
    "document itself can still identify its origin. For a strong guarantee, "
    "export the pages as images and rebuild the PDF."
)


@dataclass
class Metadata:
    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    creator: str = ""
    producer: str = ""
    creationDate: str = ""
    modDate: str = ""

    # read-only extras
    pages: int = 0
    file_size: int = 0
    pdf_version: str = ""
    encrypted: bool = False
    has_xmp: bool = False
    has_forms: bool = False
    has_attachments: bool = False
    page_size: str = ""
    permissions: dict = field(default_factory=dict)

    def editable(self) -> dict[str, str]:
        return {k: getattr(self, k) for k in FIELDS}


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------

_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?")


def parse_pdf_date(value: str) -> datetime | None:
    if not value:
        return None
    m = _PDF_DATE.match(str(value).strip())
    if not m:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(str(value).strip(), fmt)
            except ValueError:
                continue
        return None
    parts = [int(p) if p else d for p, d in
             zip(m.groups(), (1970, 1, 1, 0, 0, 0))]
    try:
        return datetime(*parts)                                # type: ignore[arg-type]
    except ValueError:
        return None


def format_pdf_date(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.strftime("D:%Y%m%d%H%M%S")


def humanize_date(value: str) -> str:
    dt = parse_pdf_date(value)
    return dt.strftime("%d %b %Y, %H:%M") if dt else (value or "—")


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

def read_metadata(source: str | Path, password: str | None = None) -> Metadata:
    src = Path(source)
    meta = Metadata(file_size=utils.file_size(src))
    with document(src, password) as doc:
        raw = doc.metadata or {}
        for key in FIELDS:
            setattr(meta, key, str(raw.get(key) or ""))
        meta.pages = doc.page_count
        meta.encrypted = was_encrypted(doc)   # never read needs_pass post-auth
        meta.pdf_version = str(raw.get("format") or "")
        meta.permissions = describe_permissions(doc)
        try:
            meta.has_xmp = bool(doc.xref_xml_metadata())
        except Exception:
            meta.has_xmp = False
        try:
            meta.has_forms = bool(doc.is_form_pdf)
        except Exception:
            meta.has_forms = False
        try:
            meta.has_attachments = doc.embfile_count() > 0
        except Exception:
            meta.has_attachments = False
        if doc.page_count:
            r = doc[0].rect
            meta.page_size = f"{r.width:.0f} × {r.height:.0f} pt"
    return meta


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

def write_metadata(
    ctx: JobContext,
    source: str | Path,
    values: dict[str, str],
    output: str | Path | None = None,
    password: str | None = None,
    update_modified: bool = True,
) -> Path:
    src = Path(source)
    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "metadata", ".pdf")
    )
    utils.ensure_parent(out)

    ctx.stage("Updating metadata")
    ctx.progress(35, "Updating metadata")

    with document(src, password) as doc:
        current = dict(doc.metadata or {})
        for key in FIELDS:
            if key in values:
                current[key] = values[key] or ""
        if update_modified:
            current["modDate"] = format_pdf_date(datetime.now())
        try:
            doc.set_metadata(current)
        except Exception as e:
            raise JobError(f"Could not write metadata: {e}") from e
        ctx.progress(80, "Saving")
        doc.save(str(out), garbage=3, deflate=True)

    ctx.progress(100, "Metadata updated")
    return out


def remove_metadata(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    password: str | None = None,
    *,
    clear_xmp: bool = True,
    clear_bookmarks: bool = False,
    clear_attachments: bool = False,
    clear_annotations: bool = False,
) -> Path:
    """Strip the document information dictionary and, optionally, more."""
    src = Path(source)
    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "clean", ".pdf")
    )
    utils.ensure_parent(out)

    ctx.stage("Removing metadata")
    ctx.progress(25, "Removing metadata")

    with document(src, password) as doc:
        try:
            doc.set_metadata({})
        except Exception:
            pass
        if clear_xmp:
            try:
                doc.del_xml_metadata()
            except Exception:
                pass
        if clear_bookmarks:
            try:
                doc.set_toc([])
            except Exception:
                pass
        if clear_attachments:
            try:
                for name in list(doc.embfile_names()):
                    doc.embfile_del(name)
            except Exception:
                pass
        if clear_annotations:
            for pno in range(doc.page_count):
                try:
                    page = doc[pno]
                    for annot in list(page.annots() or []):
                        page.delete_annot(annot)
                except Exception:
                    continue

        ctx.progress(75, "Rewriting document")
        # garbage=4 drops orphaned objects so stripped values cannot linger.
        doc.save(str(out), garbage=4, deflate=True, clean=True)

    ctx.progress(100, "Metadata removed")
    return out


def batch_remove_metadata(ctx: JobContext, sources, out_dir: Path | None = None) -> list[Path]:
    outs: list[Path] = []
    files = [Path(s) for s in sources]
    ctx.set_total(len(files), "Cleaning files")
    for f in files:
        ctx.check_cancel()
        target = (Path(out_dir) / f"{f.stem}_clean.pdf") if out_dir else None
        try:
            outs.append(remove_metadata(ctx, f, target))
        except Exception as e:
            ctx.warn(f"{f.name}: {e}")
        ctx.step(detail=f.name)
    return outs


def compare_metadata(a: Metadata, b: Metadata) -> list[tuple[str, str, str]]:
    rows = []
    for key in FIELDS:
        va, vb = getattr(a, key, ""), getattr(b, key, "")
        if va != vb:
            rows.append((FIELD_LABELS.get(key, key), va or "—", vb or "—"))
    return rows


def to_dict(meta: Metadata) -> dict:
    return asdict(meta)
