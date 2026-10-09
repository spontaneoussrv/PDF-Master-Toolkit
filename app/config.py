"""Typed application settings backed by the SQLite settings table."""

from __future__ import annotations

from typing import Any

from app import db, paths

DEFAULTS: dict[str, Any] = {
    # appearance
    "theme": "Light",                     # Light | Dark | System
    "ui_scale": 1.0,
    "sidebar_collapsed": False,
    "favourite_tools": [],
    "remember_window": True,
    "window_geometry": "",

    # output
    "output_mode": "same_folder",         # same_folder | fixed | ask
    "output_dir": str(paths.default_output_dir()),
    "overwrite_policy": "unique",         # unique | ask | overwrite
    "open_output_after": True,
    "output_suffix_style": "descriptive", # descriptive | none

    # processing
    "worker_threads": 0,                  # 0 = auto (cpu-1)
    "render_dpi": 150,
    "thumb_dpi": 48,
    "max_preview_pages": 0,               # 0 = unlimited (lazy anyway)

    # OCR
    "ocr_languages": ["eng"],
    "ocr_dpi": 300,
    "ocr_mode": "searchable",             # searchable | text | both
    "ocr_deskew": True,
    "ocr_rotate": True,
    "ocr_denoise": False,
    "ocr_contrast": False,
    "ocr_grayscale": True,
    "ocr_binarize": False,
    "ocr_psm": 3,
    "ocr_oem": 3,
    "ocr_skip_text_pages": True,
    "tesseract_path": "",                 # blank = auto-detect

    # compression
    "compress_profile": "balanced",
    "compress_dpi": 150,
    "compress_jpeg_quality": 72,
    "ghostscript_path": "",               # blank = auto-detect

    # privacy
    "history_enabled": True,
    "history_retention_days": 180,
    "store_paths_in_history": True,

    # safety
    "confirm_overwrite": True,
    "backup_before_redact": True,
    "warn_large_files_mb": 200,

    # misc
    "check_updates": False,
    "first_run_complete": False,
    "default_page_size": "A4",
}


def get(key: str, default: Any = None) -> Any:
    if default is None:
        default = DEFAULTS.get(key)
    return db.get_setting(key, default)


def set(key: str, value: Any) -> None:  # noqa: A001 - deliberate API shape
    db.set_setting(key, value)


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get(key, default))
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(get(key, default))
    except (TypeError, ValueError):
        return default


def get_bool(key: str, default: bool = False) -> bool:
    return bool(get(key, default))


def get_list(key: str, default: list | None = None) -> list:
    v = get(key, default if default is not None else [])
    return list(v) if isinstance(v, (list, tuple)) else []


def reset_to_defaults() -> None:
    for k, v in DEFAULTS.items():
        if k in ("first_run_complete", "window_geometry"):
            continue
        db.set_setting(k, v)


def worker_count() -> int:
    import os
    n = get_int("worker_threads", 0)
    if n > 0:
        return n
    return max(1, (os.cpu_count() or 2) - 1)


def export_settings() -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in db.all_settings().items() if k in DEFAULTS})
    return out


def import_settings(data: dict) -> int:
    n = 0
    for k, v in data.items():
        if k in DEFAULTS:
            db.set_setting(k, v)
            n += 1
    return n
