"""
PDF recovery.

Escalating strategies, stopping at the first that produces a readable file:

 1. clean rewrite            — fixes bad xref tables and broken object offsets
 2. qpdf structural repair   — reconstructs the cross-reference table
 3. page-by-page salvage     — copies every page that can still be parsed
 4. raw stream reconstruction— rebuilds from recoverable page objects

The result always reports how many pages were recovered *and how many were
lost*. There is no claim that every damaged PDF can be repaired, because that
is not true.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core import deps, utils
from app.core.jobs import JobContext, JobError
from app.core.pdfbase import pymupdf


@dataclass
class DiagnosisIssue:
    severity: str            # info | warning | error
    message: str


@dataclass
class Diagnosis:
    readable: bool = False
    encrypted: bool = False
    page_count: int = 0
    readable_pages: int = 0
    broken_pages: list[int] = field(default_factory=list)
    issues: list[DiagnosisIssue] = field(default_factory=list)
    file_size: int = 0
    pdf_version: str = ""
    needs_repair: bool = False

    @property
    def summary(self) -> str:
        if not self.readable:
            return "This file could not be opened as a PDF."
        if not self.needs_repair:
            return f"No problems detected — {self.page_count} page(s) readable."
        return (f"{self.readable_pages} of {self.page_count} page(s) readable; "
                f"{len(self.broken_pages)} damaged.")


@dataclass
class RepairResult:
    output: Path | None = None
    method: str = ""
    pages_recovered: int = 0
    pages_failed: int = 0
    original_pages: int = 0
    warnings: list[str] = field(default_factory=list)
    message: str = ""
    success: bool = False


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------

def diagnose(ctx: JobContext, source: str | Path, password: str | None = None) -> Diagnosis:
    src = Path(source)
    d = Diagnosis(file_size=utils.file_size(src))

    if not src.exists():
        d.issues.append(DiagnosisIssue("error", "File not found."))
        return d
    if d.file_size == 0:
        d.issues.append(DiagnosisIssue("error", "The file is empty (0 bytes)."))
        return d

    try:
        head = src.open("rb").read(1024)
        if not head.lstrip().startswith(b"%PDF"):
            d.issues.append(DiagnosisIssue(
                "warning", "The file does not start with a PDF header — it may be "
                           "truncated, or not a PDF at all."))
    except OSError as e:
        d.issues.append(DiagnosisIssue("error", f"Cannot read the file: {e}"))
        return d

    try:
        tail = _read_tail(src, 2048)
        if b"%%EOF" not in tail:
            d.issues.append(DiagnosisIssue(
                "warning", "No end-of-file marker — the download or copy was "
                           "probably incomplete."))
    except OSError:
        pass

    doc = None
    try:
        doc = pymupdf.open(str(src))
    except Exception as e:
        d.issues.append(DiagnosisIssue("error", f"PDF structure is damaged: {e}"))
        return d

    try:
        d.readable = True
        d.encrypted = bool(doc.needs_pass)
        if d.encrypted:
            if password is None or not doc.authenticate(password):
                d.issues.append(DiagnosisIssue(
                    "warning", "Encrypted. Enter the password to check the pages."))
                return d

        d.page_count = doc.page_count
        d.pdf_version = str((doc.metadata or {}).get("format", ""))
        if d.page_count == 0:
            d.issues.append(DiagnosisIssue("error", "The document contains no pages."))
            d.needs_repair = True
            return d

        ctx.set_total(d.page_count, "Checking pages")
        for i in range(d.page_count):
            ctx.check_cancel()
            try:
                page = doc[i]
                page.get_text("text")
                page.get_contents()
                d.readable_pages += 1
            except Exception:
                d.broken_pages.append(i + 1)
            ctx.step()

        if d.broken_pages:
            d.needs_repair = True
            d.issues.append(DiagnosisIssue(
                "error", f"{len(d.broken_pages)} page(s) could not be parsed: "
                         f"{utils.format_page_ranges(d.broken_pages)}"))
        try:
            if doc.is_repaired:
                d.needs_repair = True
                d.issues.append(DiagnosisIssue(
                    "warning", "The cross-reference table had to be rebuilt to open "
                               "this file. Saving a repaired copy will make it stable."))
        except AttributeError:
            pass
    finally:
        if doc is not None:
            doc.close()

    if not d.issues:
        d.issues.append(DiagnosisIssue("info", "No structural problems detected."))
    ctx.progress(100, d.summary)
    return d


def _read_tail(path: Path, n: int) -> bytes:
    with path.open("rb") as f:
        size = path.stat().st_size
        f.seek(max(0, size - n))
        return f.read()


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------

def repair(
    ctx: JobContext,
    source: str | Path,
    output: str | Path | None = None,
    password: str | None = None,
    deep: bool = True,
) -> RepairResult:
    src = Path(source)
    out = utils.resolve_collision(
        Path(output) if output else utils.suggest_output(src, "repaired", ".pdf")
    )
    utils.ensure_parent(out)
    result = RepairResult()

    strategies = [
        ("clean rewrite", _strategy_rewrite),
        ("structural repair (qpdf)", _strategy_qpdf),
        ("page-by-page salvage", _strategy_salvage),
    ]
    if deep:
        strategies.append(("raw reconstruction", _strategy_raw))

    errors: list[str] = []
    for i, (name, fn) in enumerate(strategies):
        ctx.check_cancel()
        ctx.stage(f"Trying {name}")
        ctx.progress(int(10 + i * 80 / len(strategies)), f"Trying {name}")
        try:
            ok = fn(ctx, src, out, password, result)
        except Exception as e:
            errors.append(f"{name}: {e}")
            ok = False
        if ok and _verify(out):
            result.method = name
            result.success = True
            break
        errors.append(f"{name}: produced no usable output")

    if not result.success:
        raise JobError(
            "This file could not be repaired.\n\n"
            "Recovery was attempted with " + str(len(strategies)) + " different "
            "methods. The damage is beyond what can be reconstructed — the "
            "original data is most likely missing rather than merely corrupted.\n\n"
            "Details:\n" + "\n".join(f"• {e}" for e in errors[-4:])
        )

    with_open = pymupdf.open(str(out))
    try:
        result.pages_recovered = with_open.page_count
    finally:
        with_open.close()

    if result.original_pages and result.pages_recovered < result.original_pages:
        result.pages_failed = result.original_pages - result.pages_recovered
        result.warnings.append(
            f"{result.pages_failed} page(s) could not be recovered and are not "
            f"present in the repaired file."
        )

    result.output = out
    result.message = (f"Recovered {result.pages_recovered} page(s) using {result.method}"
                      + (f"; {result.pages_failed} lost" if result.pages_failed else ""))
    ctx.progress(100, result.message)
    return result


def _verify(path: Path) -> bool:
    if not path.exists() or utils.file_size(path) < 200:
        return False
    try:
        doc = pymupdf.open(str(path))
    except Exception:
        return False
    try:
        if doc.page_count == 0:
            return False
        doc[0].get_text("text")
        return True
    except Exception:
        return False
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# strategies
# ---------------------------------------------------------------------------

def _strategy_rewrite(ctx, src: Path, out: Path, password, result: RepairResult) -> bool:
    doc = pymupdf.open(str(src))
    try:
        if doc.needs_pass and password is not None:
            doc.authenticate(password)
        result.original_pages = max(result.original_pages, doc.page_count)
        if doc.page_count == 0:
            return False
        doc.save(str(out), garbage=4, deflate=True, clean=True,
                 encryption=getattr(pymupdf, "PDF_ENCRYPT_NONE", 0))
        return True
    finally:
        doc.close()


def _strategy_qpdf(ctx, src: Path, out: Path, password, result: RepairResult) -> bool:
    qpdf = deps.qpdf_path()
    if not qpdf:
        return False
    cmd = [qpdf]
    if password:
        cmd.append(f"--password={password}")
    cmd += ["--decrypt", "--object-streams=generate", str(src), str(out)]
    proc = utils.run_cancellable(ctx, cmd, timeout=300)
    # qpdf exits 3 for "warnings but output written" -- that is a success here.
    if proc.returncode in (0, 3) and out.exists():
        if proc.returncode == 3:
            result.warnings.append("qpdf reported recoverable warnings while repairing.")
        return True
    return False


def _strategy_salvage(ctx, src: Path, out: Path, password, result: RepairResult) -> bool:
    """Copy every page that can be individually parsed into a fresh document."""
    try:
        broken = pymupdf.open(str(src))
    except Exception:
        try:
            broken = pymupdf.open(stream=src.read_bytes(), filetype="pdf")
        except Exception:
            return False

    rebuilt = pymupdf.open()
    recovered = 0
    try:
        if broken.needs_pass and password is not None:
            broken.authenticate(password)
        total = broken.page_count
        result.original_pages = max(result.original_pages, total)
        if total == 0:
            return False

        ctx.set_total(total, "Salvaging pages")
        for i in range(total):
            ctx.check_cancel()
            try:
                rebuilt.insert_pdf(broken, from_page=i, to_page=i, annots=False)
                recovered += 1
            except Exception:
                try:
                    # last resort: rasterise the page so at least its look survives
                    pix = broken[i].get_pixmap(dpi=150)
                    page = rebuilt.new_page(width=pix.width * 72 / 150,
                                            height=pix.height * 72 / 150)
                    page.insert_image(page.rect, pixmap=pix)
                    recovered += 1
                    result.warnings.append(
                        f"Page {i + 1} was damaged and recovered as an image "
                        f"(its text is no longer selectable).")
                except Exception:
                    result.warnings.append(f"Page {i + 1} could not be recovered.")
            ctx.step()

        if recovered == 0:
            return False
        rebuilt.save(str(out), garbage=4, deflate=True, clean=True)
        return True
    finally:
        rebuilt.close()
        broken.close()


def _strategy_raw(ctx, src: Path, out: Path, password, result: RepairResult) -> bool:
    """
    Reconstruct from the raw bytes.

    PyMuPDF's stream loader runs its own recovery pass that is more tolerant
    than opening by filename, so this occasionally succeeds where everything
    else fails.
    """
    try:
        data = src.read_bytes()
    except OSError:
        return False

    # Trim anything before the header; some downloads prepend HTML error text.
    idx = data.find(b"%PDF")
    if idx > 0:
        data = data[idx:]
        result.warnings.append(f"Removed {idx} junk byte(s) before the PDF header.")

    if b"%%EOF" not in data[-4096:]:
        data = data + b"\n%%EOF\n"
        result.warnings.append("Appended a missing end-of-file marker.")

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        return False
    try:
        if doc.needs_pass and password is not None:
            doc.authenticate(password)
        result.original_pages = max(result.original_pages, doc.page_count)
        if doc.page_count == 0:
            return False
        doc.save(str(out), garbage=4, deflate=True, clean=True)
        return True
    finally:
        doc.close()
