"""
External tool discovery and capability probing.

Everything here degrades gracefully: if a binary is missing the app still
starts, the affected feature explains what is unavailable, and no module
raises at import time. `report()` powers Settings -> Diagnostics.
"""

from __future__ import annotations

import functools
import os
import shutil
import sys
from pathlib import Path

from app import config, paths
from app.core.utils import run_quiet

# ---------------------------------------------------------------------------
# generic locator
# ---------------------------------------------------------------------------

def _first_existing(candidates) -> str | None:
    for c in candidates:
        if not c:
            continue
        p = Path(c)
        if p.is_file():
            return str(p)
    return None


def _glob_first(root: Path, pattern: str) -> str | None:
    try:
        matches = sorted(root.glob(pattern), reverse=True)
        for m in matches:
            if m.is_file():
                return str(m)
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# Tesseract
# ---------------------------------------------------------------------------

#: Where the OCR engine can be obtained, shown to the user when it is missing.
TESSERACT_DOWNLOAD_URL = "https://github.com/UB-Mannheim/tesseract/wiki"


def _registry_tesseract() -> str | None:
    """Read the install directory the official Tesseract installer records."""
    if os.name != "nt":
        return None
    try:
        import winreg  # type: ignore
    except ImportError:
        return None

    for hive, flag in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
                       (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                       (winreg.HKEY_CURRENT_USER, 0)):
        for subkey in (r"SOFTWARE\Tesseract-OCR", r"SOFTWARE\WOW6432Node\Tesseract-OCR"):
            try:
                with winreg.OpenKey(hive, subkey, 0,
                                    winreg.KEY_READ | flag) as key:
                    value, _ = winreg.QueryValueEx(key, "InstallDir")
                    candidate = Path(value) / "tesseract.exe"
                    if candidate.is_file():
                        return str(candidate)
            except OSError:
                continue
    return None


@functools.lru_cache(maxsize=1)
def _tesseract_auto() -> str | None:
    exe = "tesseract.exe" if os.name == "nt" else "tesseract"

    # 1. bundled copy shipped by the installer -- always preferred
    bundled = paths.vendor_dir() / "tesseract" / exe
    if bundled.is_file():
        return str(bundled)

    # 2. PATH
    found = shutil.which("tesseract")
    if found:
        return found

    if os.name != "nt":
        return None

    # 3. whatever the official installer wrote to the registry
    found = _registry_tesseract()
    if found:
        return found

    # 4. every install location the common package managers use
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    home = Path.home()
    return _first_existing([
        Path(pf) / "Tesseract-OCR" / exe,                       # default installer
        Path(pf86) / "Tesseract-OCR" / exe,
        Path(local) / "Programs" / "Tesseract-OCR" / exe,       # per-user install
        Path(local) / "Tesseract-OCR" / exe,
        Path(r"C:\tools\tesseract") / exe,                      # chocolatey
        Path(r"C:\ProgramData\chocolatey\bin") / exe,
        home / "scoop" / "apps" / "tesseract" / "current" / exe,  # scoop
        home / "scoop" / "shims" / exe,
        Path(local) / "Microsoft" / "WinGet" / "Links" / exe,     # winget shim
    ])


def tesseract_path() -> str | None:
    """User override wins, then bundled, then PATH, then known locations."""
    manual = (config.get("tesseract_path") or "").strip()
    if manual and Path(manual).is_file():
        return manual
    return _tesseract_auto()


def has_tesseract() -> bool:
    return tesseract_path() is not None


@functools.lru_cache(maxsize=4)
def _tessdata_for(exe: str) -> str | None:
    """Locate tessdata next to a bundled tesseract so TESSDATA_PREFIX is right."""
    p = Path(exe).parent
    for cand in (p / "tessdata", p.parent / "tessdata", p / "share" / "tessdata"):
        if cand.is_dir():
            return str(cand)
    return None


def tesseract_env() -> dict:
    env = dict(os.environ)
    exe = tesseract_path()
    if exe:
        td = _tessdata_for(exe)
        if td:
            env["TESSDATA_PREFIX"] = td
    return env


def tesseract_version() -> str:
    exe = tesseract_path()
    if not exe:
        return "not found"
    try:
        r = run_quiet([exe, "--version"], timeout=15, env=tesseract_env())
        line = (r.stdout or r.stderr or "").strip().splitlines()
        return line[0] if line else "unknown"
    except Exception as e:
        return f"error: {e}"


# Human names for the language codes Tesseract ships.
LANGUAGE_NAMES = {
    "eng": "English", "hin": "Hindi", "ben": "Bengali", "spa": "Spanish",
    "fra": "French", "deu": "German", "ita": "Italian", "por": "Portuguese",
    "nld": "Dutch", "rus": "Russian", "ara": "Arabic", "chi_sim": "Chinese (Simplified)",
    "chi_tra": "Chinese (Traditional)", "jpn": "Japanese", "kor": "Korean",
    "tam": "Tamil", "tel": "Telugu", "mar": "Marathi", "guj": "Gujarati",
    "kan": "Kannada", "mal": "Malayalam", "pan": "Punjabi", "urd": "Urdu",
    "ori": "Odia", "asm": "Assamese", "nep": "Nepali", "san": "Sanskrit",
    "tha": "Thai", "vie": "Vietnamese", "ind": "Indonesian", "msa": "Malay",
    "tur": "Turkish", "pol": "Polish", "ces": "Czech", "swe": "Swedish",
    "dan": "Danish", "nor": "Norwegian", "fin": "Finnish", "ell": "Greek",
    "heb": "Hebrew", "fas": "Persian", "ukr": "Ukrainian", "ron": "Romanian",
    "hun": "Hungarian", "bul": "Bulgarian", "cat": "Catalan", "srp": "Serbian",
    "hrv": "Croatian", "slk": "Slovak", "slv": "Slovenian", "lit": "Lithuanian",
    "lav": "Latvian", "est": "Estonian", "afr": "Afrikaans", "swa": "Swahili",
    "sin": "Sinhala", "mya": "Burmese", "khm": "Khmer", "lao": "Lao",
    "osd": "Orientation & script detection", "equ": "Math / equations",
}

# Offered in the OCR language picker even when the pack is not installed yet,
# so the UI can tell the user exactly which pack to add.
PRIORITY_LANGS = ["eng", "hin", "ben", "spa", "fra", "deu", "ita", "por"]


def installed_languages() -> list[str]:
    """Language codes with a trained model available to this Tesseract."""
    exe = tesseract_path()
    if not exe:
        return []
    try:
        r = run_quiet([exe, "--list-langs"], timeout=20, env=tesseract_env())
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        langs = []
        for line in out.splitlines():
            line = line.strip()
            if not line or " " in line or line.endswith(":"):
                continue
            langs.append(line)
        return sorted(set(langs))
    except Exception:
        return []


def language_label(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


# ---------------------------------------------------------------------------
# Ghostscript  (optional high-power compression backend)
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _ghostscript_auto() -> str | None:
    bundled = paths.vendor_dir() / "gs" / ("gswin64c.exe" if os.name == "nt" else "gs")
    if bundled.is_file():
        return str(bundled)

    for name in ("gswin64c", "gswin32c", "gs"):
        found = shutil.which(name)
        if found:
            return found

    if os.name == "nt":
        for root in (r"C:\Program Files\gs", r"C:\Program Files (x86)\gs"):
            hit = _glob_first(Path(root), "gs*/bin/gswin64c.exe") or \
                  _glob_first(Path(root), "gs*/bin/gswin32c.exe")
            if hit:
                return hit
    return None


def ghostscript_path() -> str | None:
    manual = (config.get("ghostscript_path") or "").strip()
    if manual and Path(manual).is_file():
        return manual
    return _ghostscript_auto()


def has_ghostscript() -> bool:
    return ghostscript_path() is not None


def ghostscript_version() -> str:
    exe = ghostscript_path()
    if not exe:
        return "not found"
    try:
        r = run_quiet([exe, "--version"], timeout=15)
        return (r.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# qpdf  (structural repair / linearization)
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def qpdf_path() -> str | None:
    bundled = paths.vendor_dir() / "qpdf" / ("qpdf.exe" if os.name == "nt" else "qpdf")
    if bundled.is_file():
        return str(bundled)
    return shutil.which("qpdf")


def has_qpdf() -> bool:
    return qpdf_path() is not None


# ---------------------------------------------------------------------------
# LibreOffice  (Office conversion fallback when MS Word is absent)
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def soffice_path() -> str | None:
    found = shutil.which("soffice") or shutil.which("soffice.exe")
    if found:
        return found
    if os.name == "nt":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        return _first_existing([
            Path(pf) / "LibreOffice" / "program" / "soffice.exe",
            Path(pf86) / "LibreOffice" / "program" / "soffice.exe",
        ])
    return None


def has_soffice() -> bool:
    return soffice_path() is not None


# ---------------------------------------------------------------------------
# Microsoft Office COM automation
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def has_msword() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg  # type: ignore
        for hive in (winreg.HKEY_CLASSES_ROOT,):
            try:
                winreg.OpenKey(hive, r"Word.Application")
                return True
            except OSError:
                continue
    except Exception:
        pass
    return False


@functools.lru_cache(maxsize=1)
def has_msexcel() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg  # type: ignore
        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Excel.Application")
        return True
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def has_pywin32() -> bool:
    if os.name != "nt":
        return False
    try:
        import win32com.client  # noqa: F401
        return True
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def has_wia() -> bool:
    """Windows Image Acquisition -- required for Scan to PDF."""
    if not has_pywin32():
        return False
    try:
        import win32com.client  # type: ignore
        win32com.client.Dispatch("WIA.DeviceManager")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# optional Python packages
# ---------------------------------------------------------------------------

def _mod(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


@functools.lru_cache(maxsize=32)
def has_module(name: str) -> bool:
    return _mod(name)


def has_pdf2docx() -> bool:
    return has_module("pdf2docx")


def has_pdfplumber() -> bool:
    return has_module("pdfplumber")


def has_pyhanko() -> bool:
    return has_module("pyhanko")


def has_docx() -> bool:
    return has_module("docx")


def has_openpyxl() -> bool:
    return has_module("openpyxl")


def has_reportlab() -> bool:
    return has_module("reportlab")


def has_numpy() -> bool:
    return has_module("numpy")


def has_cv2() -> bool:
    return has_module("cv2")


# ---------------------------------------------------------------------------
# capability summary
# ---------------------------------------------------------------------------

def capabilities() -> dict[str, bool]:
    return {
        "ocr": has_tesseract(),
        "ghostscript_compression": has_ghostscript(),
        "structural_repair": has_qpdf(),
        "pdf_to_word_layout": has_pdf2docx(),
        "table_extraction": has_pdfplumber(),
        "word_to_pdf_office": has_msword() and has_pywin32(),
        "word_to_pdf_libreoffice": has_soffice(),
        "excel_to_pdf_office": has_msexcel() and has_pywin32(),
        "scanner": has_wia(),
        "certificate_signing": has_pyhanko(),
        "image_preprocessing": has_numpy(),
    }


def missing_optional() -> list[tuple[str, str]]:
    """(feature, remedy) pairs for anything unavailable."""
    out = []
    if not has_tesseract():
        out.append(("OCR", "Tesseract OCR was not found. Reinstall PDF Master Toolkit "
                           "with the OCR component enabled, or set the path in Settings."))
    if not has_ghostscript():
        out.append(("Maximum compression", "Ghostscript is not installed. The built-in "
                                           "compressor is used instead."))
    if not has_qpdf():
        out.append(("Deep repair", "qpdf is not installed. Structural repair falls back "
                                   "to the built-in rewriter."))
    if not (has_msword() or has_soffice()):
        out.append(("Word to PDF (high fidelity)", "Neither Microsoft Word nor LibreOffice "
                                                   "was detected. A built-in renderer is used, "
                                                   "which handles simple documents only."))
    if not has_wia():
        out.append(("Scan to PDF", "No WIA scanner interface available on this system."))
    if not has_pyhanko():
        out.append(("Certificate signing", "pyHanko is not installed. Visual signatures "
                                           "remain available."))
    return out


def report() -> str:
    """Plain-text diagnostics, copied to the clipboard from Settings."""
    from app import branding

    lines = [
        f"{branding.APP_NAME} {branding.APP_VERSION} (build {branding.APP_BUILD})",
        f"Python  : {sys.version.split()[0]}",
        f"Platform: {sys.platform} / {os.name}",
        f"Frozen  : {paths.is_frozen()}",
        f"App dir : {paths.app_root()}",
        f"Data dir: {paths.user_data_dir()}",
        "",
        "--- external tools ---",
        f"Tesseract   : {tesseract_path() or 'not found'}",
        f"  version   : {tesseract_version()}",
        f"  languages : {', '.join(installed_languages()) or 'none detected'}",
        f"Ghostscript : {ghostscript_path() or 'not found'} ({ghostscript_version()})",
        f"qpdf        : {qpdf_path() or 'not found'}",
        f"LibreOffice : {soffice_path() or 'not found'}",
        f"MS Word     : {'detected' if has_msword() else 'not detected'}",
        f"MS Excel    : {'detected' if has_msexcel() else 'not detected'}",
        f"WIA scanner : {'available' if has_wia() else 'unavailable'}",
        "",
        "--- python packages ---",
    ]
    for m in ("fitz", "pymupdf", "pypdf", "PIL", "openpyxl", "docx", "pdfplumber",
              "reportlab", "pdf2docx", "numpy", "pyhanko", "win32com"):
        lines.append(f"{m:<12}: {'yes' if has_module(m) else 'no'}")

    lines.append("")
    lines.append("--- capabilities ---")
    for k, v in capabilities().items():
        lines.append(f"{k:<28}: {'ok' if v else 'unavailable'}")
    return "\n".join(lines)


def refresh() -> None:
    """Clear caches after the user changes a path in Settings."""
    for fn in (_tesseract_auto, _ghostscript_auto, qpdf_path, soffice_path,
               has_msword, has_msexcel, has_pywin32, has_wia, has_module, _tessdata_for):
        try:
            fn.cache_clear()  # type: ignore[attr-defined]
        except AttributeError:
            pass
