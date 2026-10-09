# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build specification for PDF Master Toolkit.

Produces a one-DIRECTORY build (dist/PDF Master Toolkit/) rather than one file.
That matters here: a one-file build unpacks ~250 MB to a temp folder on every
launch, which makes a heavy PDF app feel slow to start, and Tesseract's
language data cannot be updated by the user afterwards. Inno Setup then wraps
the folder into a single installer, so the end user still gets one .exe to run.

Build with:
    pyinstaller PDF_Master_Toolkit.spec --noconfirm
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

sys.path.insert(0, str(Path(SPECPATH)))
from app import branding                                        # noqa: E402

ROOT = Path(SPECPATH)
BLOCK_CIPHER = None
DEBUG_CONSOLE = False          # set True to see a console window while testing


# ---------------------------------------------------------------------------
# data files
# ---------------------------------------------------------------------------

datas = [
    (str(ROOT / "assets"), "assets"),
]

# Bundled third-party binaries, if the build machine has staged them.
# tools/fetch_vendor.py explains how to populate this folder.
vendor = ROOT / "vendor"
if vendor.is_dir() and any(vendor.iterdir()):
    datas.append((str(vendor), "vendor"))
    print(f"[spec] bundling vendor tools from {vendor}")
else:
    print("[spec] NOTE: no vendor/ folder - Tesseract and Ghostscript will be "
          "looked up on the target machine instead of bundled.")

# pdf2docx ships its own data; pdfplumber pulls in pdfminer resources
for pkg in ("pdf2docx", "pdfminer", "pdfplumber", "fontTools", "reportlab"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# hidden imports
# ---------------------------------------------------------------------------

hiddenimports = [
    # our own lazily-imported page modules -- PyInstaller cannot see
    # importlib.import_module() calls, so name them all explicitly
    "app.ui.pages.dashboard",
    "app.ui.pages.viewer_page",
    "app.ui.pages.merge",
    "app.ui.pages.split",
    "app.ui.pages.extract_pages",
    "app.ui.pages.organize",
    "app.ui.pages.rotate",
    "app.ui.pages.crop",
    "app.ui.pages.pagesize",
    "app.ui.pages.ocr_page",
    "app.ui.pages.extract_text",
    "app.ui.pages.extract_images",
    "app.ui.pages.convert_pages",
    "app.ui.pages.watermark",
    "app.ui.pages.headerfooter",
    "app.ui.pages.pagenumbers",
    "app.ui.pages.sign",
    "app.ui.pages.redact",
    "app.ui.pages.protect",
    "app.ui.pages.unlock",
    "app.ui.pages.metadata_page",
    "app.ui.pages.compress_page",
    "app.ui.pages.repair_page",
    "app.ui.pages.compare_page",
    "app.ui.pages.scan_page",
    "app.ui.pages.batch_page",
    "app.ui.pages.workflow_page",
    "app.ui.pages.visits_page",
    "app.ui.pages.history_page",
    "app.ui.pages.settings_page",
    "app.ui.pages.about_page",

    # third-party bits that are imported dynamically
    "pymupdf",
    "fitz",
    "PIL._tkinter_finder",
    "openpyxl.cell._writer",
    "docx",
    "pdf2docx",
    "pdfplumber",
    "reportlab.graphics.barcode",
]

# Windows COM automation for Word/Excel and WIA scanning
if sys.platform == "win32":
    hiddenimports += [
        "win32com", "win32com.client", "pythoncom", "pywintypes", "win32api",
    ]

# certificate signing is optional; include it only if installed
try:
    import pyhanko                                              # noqa: F401
    hiddenimports += collect_submodules("pyhanko")
    hiddenimports += collect_submodules("pyhanko_certvalidator")
except ImportError:
    print("[spec] pyHanko not installed - certificate signing will be disabled.")


# ---------------------------------------------------------------------------
# exclusions -- keeps the installer to a sane size
# ---------------------------------------------------------------------------

excludes = [
    "tkinter", "unittest", "pydoc", "doctest", "test", "tests",
    "matplotlib", "scipy", "pandas", "IPython", "jupyter", "notebook",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtTest", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
]


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------

a = Analysis(
    ["app/main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=BLOCK_CIPHER)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PDF Master Toolkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # UPX trips antivirus heuristics; not worth it
    console=DEBUG_CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / branding.ICON_FILE),
    version=str(ROOT / "build_version.txt") if (ROOT / "build_version.txt").exists() else None,
    uac_admin=False,                 # the app never needs administrator rights
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PDF Master Toolkit",
)
