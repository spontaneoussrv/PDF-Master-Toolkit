"""
Encryption, permissions and password removal.

Deliberate limitation: there is no password recovery, brute-force or bypass
anywhere in this module. Removing protection requires the correct password, or
a document whose owner-password restrictions the user is authorised to change.
Requests to "crack" a PDF are not supported by design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core import utils
from app.core.jobs import JobContext, JobError
from app.core.pdfbase import (
    PdfLocked, build_permissions, describe_permissions, document, open_pdf,
    PERMISSION_LABELS, pymupdf,
)
from app.core.pdfbase import was_encrypted as was_encrypted_flag

# PyMuPDF encryption constants, resolved defensively for older builds.
ENCRYPTION_METHODS = {
    "aes256": (getattr(pymupdf, "PDF_ENCRYPT_AES_256", 6),
               "AES 256-bit (PDF 2.0) — strongest, needs a modern reader"),
    "aes128": (getattr(pymupdf, "PDF_ENCRYPT_AES_128", 5),
               "AES 128-bit (Acrobat 7+) — strong and widely compatible"),
    "rc4_128": (getattr(pymupdf, "PDF_ENCRYPT_RC4_128", 4),
                "RC4 128-bit (legacy) — use only for very old readers"),
}
DEFAULT_METHOD = "aes256"

PERMISSION_ORDER = ["print", "print_hq", "copy", "modify", "annotate",
                    "form", "assemble", "accessibility"]


@dataclass
class ProtectOptions:
    user_password: str = ""            # open password
    owner_password: str = ""           # permissions password
    method: str = DEFAULT_METHOD
    allow: dict[str, bool] = field(default_factory=lambda: {
        "print": True, "print_hq": True, "copy": False, "modify": False,
        "annotate": False, "form": True, "assemble": False, "accessibility": True,
    })


@dataclass
class SecurityResult:
    output: Path | None = None
    pages: int = 0
    encrypted: bool = False
    method: str = ""
    message: str = ""
    warnings: list[str] = field(default_factory=list)


PERMISSION_DISCLAIMER = (
    "Permission settings are instructions to the PDF reader, not a hard lock. "
    "Adobe Acrobat and most mainstream readers honour them; some third-party "
    "tools do not. For content that must stay private, set an open password as "
    "well — that genuinely encrypts the file."
)


# ---------------------------------------------------------------------------
# protect
# ---------------------------------------------------------------------------

def protect(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    opts: ProtectOptions | None = None,
    password: str | None = None,
) -> SecurityResult:
    src = Path(source)
    opts = opts or ProtectOptions()

    if not opts.user_password and not opts.owner_password:
        raise JobError(
            "Set at least one password.\n\n"
            "• Open password — required to view the document.\n"
            "• Permissions password — required to change restrictions."
        )

    method_key = opts.method if opts.method in ENCRYPTION_METHODS else DEFAULT_METHOD
    method_value, method_label = ENCRYPTION_METHODS[method_key]

    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "protected", ".pdf")
    )
    utils.ensure_parent(out)

    result = SecurityResult(method=method_label)
    ctx.stage("Applying encryption")
    ctx.progress(30, "Applying encryption")

    with document(src, password) as doc:
        result.pages = doc.page_count
        perm = build_permissions(opts.allow)
        try:
            doc.save(
                str(out),
                encryption=method_value,
                owner_pw=opts.owner_password or opts.user_password,
                user_pw=opts.user_password or None,
                permissions=perm,
                garbage=3,
                deflate=True,
            )
        except Exception as e:
            raise JobError(f"Could not encrypt this document: {e}") from e

    result.output = out
    result.encrypted = True
    denied = [PERMISSION_LABELS[k] for k in PERMISSION_ORDER if not opts.allow.get(k, True)]
    result.message = f"Protected with {method_label.split('—')[0].strip()}"
    if denied:
        result.message += f" · restricted: {', '.join(denied)}"
    result.warnings.append(PERMISSION_DISCLAIMER)
    ctx.progress(100, result.message)
    return result


# ---------------------------------------------------------------------------
# unlock
# ---------------------------------------------------------------------------

def unlock(
    ctx: JobContext,
    source: str | Path,
    password: str,
    output: str | Path | None = None,
    keep_permissions: bool = False,
) -> SecurityResult:
    """
    Remove encryption from a PDF the user can already open.

    The password must be correct — this is a decrypt-and-resave, not a bypass.
    A PDF with only owner restrictions (no open password) can be unlocked with
    an empty password, which is legitimate: the user already has full read
    access and is choosing to lift restrictions on their own document.
    """
    src = Path(source)
    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "unlocked", ".pdf")
    )
    utils.ensure_parent(out)

    ctx.stage("Verifying password")
    ctx.progress(20, "Verifying password")

    try:
        doc = open_pdf(src, password)
    except PdfLocked:
        raise JobError(
            "That password was not accepted.\n\n"
            "PDF Master Toolkit does not attempt to guess or recover PDF "
            "passwords. Enter the correct password to continue."
        ) from None

    result = SecurityResult()
    try:
        # NEVER read doc.needs_pass here: on an authenticated document it
        # re-runs the password check and destroys the AES decryption state, so
        # the file saved below comes out completely blank. open_pdf() recorded
        # the pre-authentication state for us.
        was_encrypted = was_encrypted_flag(doc)
        result.pages = doc.page_count
        ctx.progress(60, "Removing protection")
        try:
            doc.save(str(out), encryption=getattr(pymupdf, "PDF_ENCRYPT_NONE", 0),
                     garbage=3, deflate=True)
        except Exception as e:
            raise JobError(f"Could not save the unlocked document: {e}") from e
    finally:
        doc.close()

    result.output = out
    result.encrypted = False
    result.message = ("Protection removed — the file now opens without a password"
                      if was_encrypted else
                      "This document was not encrypted; a clean copy was saved")
    ctx.progress(100, result.message)
    return result


# ---------------------------------------------------------------------------
# inspection
# ---------------------------------------------------------------------------

@dataclass
class SecurityInfo:
    encrypted: bool = False
    needs_password: bool = False
    method: str = ""
    permissions: dict[str, bool] = field(default_factory=dict)
    error: str = ""

    @property
    def restricted(self) -> list[str]:
        return [PERMISSION_LABELS[k] for k, v in self.permissions.items()
                if not v and k in PERMISSION_LABELS]


def inspect_security(source: str | Path, password: str | None = None) -> SecurityInfo:
    info = SecurityInfo()
    src = Path(source)
    try:
        doc = pymupdf.open(str(src))
    except Exception as e:
        info.error = str(e)
        return info
    try:
        # Read needs_pass ONCE, before any authenticate() call. Reading it again
        # afterwards would invalidate the decryption state (see pdfbase).
        locked = bool(doc.needs_pass)
        info.needs_password = locked
        if locked:
            info.encrypted = True
            if password is not None and doc.authenticate(password):
                info.needs_password = False
            else:
                return info
        info.encrypted = bool(getattr(doc, "is_encrypted", False)) or info.encrypted
        info.permissions = describe_permissions(doc)
        meta = doc.metadata or {}
        info.method = str(meta.get("encryption") or ("Encrypted" if info.encrypted else "None"))
    except Exception as e:
        info.error = str(e)
    finally:
        doc.close()
    return info


def change_permissions(
    ctx: JobContext,
    source: str | Path,
    allow: dict[str, bool],
    owner_password: str,
    output: str | Path | None = None,
    password: str | None = None,
    keep_open_password: str = "",
) -> SecurityResult:
    """Re-apply permission flags without changing the open password."""
    opts = ProtectOptions(
        user_password=keep_open_password,
        owner_password=owner_password,
        allow=allow,
    )
    return protect(ctx, source, output, opts, password)
