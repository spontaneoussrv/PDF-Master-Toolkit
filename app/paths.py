"""Filesystem locations. Frozen-aware (PyInstaller) and per-user safe."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app import branding


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_root() -> Path:
    """Directory containing the executable (frozen) or the repo root (dev)."""
    if is_frozen():
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """
    Where bundled read-only resources live.

    PyInstaller one-file extracts to sys._MEIPASS; one-dir keeps them beside
    the exe. Both are handled here so the rest of the code never cares.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return app_root()


def assets_dir() -> Path:
    return resource_root() / "assets"


def asset(name: str) -> Path:
    return assets_dir() / name


def vendor_dir() -> Path:
    """Bundled third-party binaries (Tesseract, Ghostscript, qpdf)."""
    return resource_root() / "vendor"


def user_data_dir() -> Path:
    """%LOCALAPPDATA%\\SheetClub\\PDF Master Toolkit on Windows."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    p = base / branding.COMPANY_NAME / branding.APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    p = user_data_dir() / "config"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = user_data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def temp_dir() -> Path:
    p = user_data_dir() / "temp"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = user_data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def signatures_dir() -> Path:
    p = user_data_dir() / "signatures"
    p.mkdir(parents=True, exist_ok=True)
    return p


def workflows_dir() -> Path:
    p = user_data_dir() / "workflows"
    p.mkdir(parents=True, exist_ok=True)
    return p


def database_path() -> Path:
    return user_data_dir() / "pdfmaster.db"


def default_output_dir() -> Path:
    """Documents\\PDF Master Toolkit -- created lazily on first save."""
    if os.name == "nt":
        docs = Path.home() / "Documents"
    else:
        docs = Path.home() / "Documents"
    p = docs / branding.APP_NAME
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        p = user_data_dir() / "output"
        p.mkdir(parents=True, exist_ok=True)
    return p
