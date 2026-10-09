"""
=============================================================================
 BRANDING CONFIGURATION  --  PDF Master Toolkit
=============================================================================

 THIS IS THE ONLY FILE YOU NEED TO EDIT TO REBRAND THE ENTIRE APPLICATION.

 Everything below feeds the window title, the sidebar header, the About page,
 the Settings page, the installer metadata (Inno Setup reads this file via
 tools/export_branding.py) and every support link in the UI.

 Values marked  # TODO  are placeholders pending the SheetClub branding
 spreadsheet. Replace the string, save, rebuild. Nothing else changes.
=============================================================================
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# PRODUCT IDENTITY
# --------------------------------------------------------------------------

APP_NAME = "PDF Master Toolkit"
APP_SHORT_NAME = "PDF Master"
APP_TAGLINE = "All-in-One PDF Productivity Suite"
APP_VERSION = "1.0.2"
APP_BUILD = "1020"

# Reverse-DNS style id, used for registry keys, AppData folder and the
# Inno Setup AppId. Change this ONLY for a genuinely different product.
APP_ID = "com.sheetclub.pdfmastertoolkit"

# Windows AppUserModelID -- controls taskbar icon grouping and jump lists.
APP_USER_MODEL_ID = "SheetClub.PDFMasterToolkit.1"


# --------------------------------------------------------------------------
# COMPANY
# --------------------------------------------------------------------------

COMPANY_NAME = "SheetClub"
COMPANY_LEGAL_NAME = "SheetClub"                    # TODO: full legal entity name
COMPANY_WEBSITE = "https://sheetclub.com"           # TODO: confirm
COMPANY_ADDRESS = ""                                # TODO: postal address
COPYRIGHT = f"Copyright © 2026 {COMPANY_NAME}. All rights reserved."


# --------------------------------------------------------------------------
# SUPPORT DETAILS
# --------------------------------------------------------------------------

SUPPORT_EMAIL = "support@sheetclub.com"             # TODO: confirm
SUPPORT_PHONE = ""                                  # TODO: confirm
SUPPORT_URL = "https://sheetclub.com/support"       # TODO: confirm
SUPPORT_HOURS = ""                                  # TODO: e.g. "Mon-Fri, 9am-6pm IST"
DOCS_URL = "https://sheetclub.com/docs"             # TODO: confirm
UPDATES_URL = "https://sheetclub.com/downloads"     # TODO: confirm
PRIVACY_URL = "https://sheetclub.com/privacy"       # TODO: confirm
EULA_URL = "https://sheetclub.com/eula"             # TODO: confirm

# Shown verbatim in the About page support card. Leave blank lines out.
SUPPORT_NOTE = (
    "For technical assistance, licensing questions or bug reports, "
    "contact our support team. Please include your license key and the "
    "diagnostics report from Settings → Diagnostics."
)


# --------------------------------------------------------------------------
# LICENSING
# --------------------------------------------------------------------------

LICENSE_MODE = "perpetual"          # perpetual | subscription | trial
LICENSE_KEY_PREFIX = "SCPMT"        # shown as SCPMT-XXXXX-XXXXX-XXXXX
TRIAL_DAYS = 14


# --------------------------------------------------------------------------
# VISUAL IDENTITY
# --------------------------------------------------------------------------
# Hex colours. The theme engine derives hovers, borders and shades from
# these automatically, so changing ACCENT alone restyles the whole app.

# Sampled directly from the SheetClub logo artwork in assets/brand/.
BRAND_DEEP = "#004033"              # darkest green in the mark
BRAND_ACCENT = "#157B48"            # primary action / active nav
BRAND_ACCENT_ALT = "#4FA84E"        # gradient partner for the accent
BRAND_BRIGHT = "#86D554"            # highlight green from the grid cells
BRAND_NAVY = "#072741"              # the "Sheet" wordmark navy

BRAND_SUCCESS = "#1B9E57"
BRAND_WARNING = "#E08600"
BRAND_DANGER = "#D93F35"
BRAND_INFO = "#0E7FA8"

# Logo files live in assets/. If a file is missing the app falls back to a
# generated monogram tile, so the build never breaks on a missing asset.
LOGO_FILE = "logo.png"              # TODO: drop the real SheetClub logo here
LOGO_MARK_FILE = "logo_mark.png"    # square mark for the sidebar
ICON_FILE = "icon.ico"              # Windows executable + installer icon
SPLASH_FILE = "splash.png"

# Monogram fallback when LOGO_MARK_FILE is absent
LOGO_MONOGRAM = "SC"


# --------------------------------------------------------------------------
# PRIVACY STATEMENT
# --------------------------------------------------------------------------
# Required wording, surfaced on both the About page and the Settings page.

PRIVACY_STATEMENT = (
    "Your PDF files are processed locally on your computer unless a feature "
    "explicitly states otherwise."
)

PRIVACY_DETAIL = (
    "PDF Master Toolkit performs all merging, splitting, OCR, compression, "
    "conversion, redaction and encryption on this machine. No document "
    "content is transmitted to SheetClub or to any third party. History "
    "records file names and operation names only — never document "
    "content — and can be disabled entirely in Settings."
)


# --------------------------------------------------------------------------
# THIRD-PARTY ATTRIBUTION  (shown on the About page)
# --------------------------------------------------------------------------

THIRD_PARTY = [
    ("Qt for Python (PySide6)", "LGPL v3", "https://www.qt.io/qt-for-python"),
    ("PyMuPDF / MuPDF", "AGPL v3 / Commercial", "https://pymupdf.readthedocs.io"),
    ("pypdf", "BSD-3-Clause", "https://pypdf.readthedocs.io"),
    ("Pillow", "MIT-CMU", "https://python-pillow.org"),
    ("Tesseract OCR", "Apache 2.0", "https://github.com/tesseract-ocr/tesseract"),
    ("Leptonica", "BSD-2-Clause", "http://leptonica.org"),
    ("openpyxl", "MIT", "https://openpyxl.readthedocs.io"),
    ("python-docx", "MIT", "https://python-docx.readthedocs.io"),
    ("pdfplumber", "MIT", "https://github.com/jsvine/pdfplumber"),
    ("ReportLab", "BSD", "https://www.reportlab.com"),
    ("Ghostscript (optional)", "AGPL v3 / Commercial", "https://ghostscript.com"),
]

LICENSING_NOTICE = (
    "PyMuPDF and Ghostscript are distributed under the AGPL. If you ship this "
    "application commercially without releasing your source code, obtain a "
    "commercial licence from Artifex, or build with the pypdf/pypdfium2 "
    "backend only (see BUILDING.md → 'AGPL-free build')."
)


# --------------------------------------------------------------------------
# DERIVED HELPERS
# --------------------------------------------------------------------------

def window_title(page: str | None = None) -> str:
    return f"{page} — {APP_NAME}" if page else f"{APP_NAME}"


def version_string() -> str:
    return f"Version {APP_VERSION} (build {APP_BUILD})"


def full_title() -> str:
    return f"{APP_NAME} {APP_VERSION}"


def as_dict() -> dict:
    """Snapshot used by tools/export_branding.py to generate installer values."""
    return {
        "APP_NAME": APP_NAME,
        "APP_SHORT_NAME": APP_SHORT_NAME,
        "APP_VERSION": APP_VERSION,
        "APP_BUILD": APP_BUILD,
        "APP_ID": APP_ID,
        "COMPANY_NAME": COMPANY_NAME,
        "COMPANY_LEGAL_NAME": COMPANY_LEGAL_NAME,
        "COMPANY_WEBSITE": COMPANY_WEBSITE,
        "SUPPORT_EMAIL": SUPPORT_EMAIL,
        "SUPPORT_URL": SUPPORT_URL,
        "UPDATES_URL": UPDATES_URL,
        "COPYRIGHT": COPYRIGHT,
        "ICON_FILE": ICON_FILE,
    }
