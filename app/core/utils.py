"""Shared helpers: page-range parsing, safe output naming, formatting."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Iterable, Sequence

from app import config

PDF_EXT = (".pdf",)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".gif")
WORD_EXT = (".docx", ".doc", ".rtf", ".odt")
EXCEL_EXT = (".xlsx", ".xls", ".xlsm", ".csv")


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------

def human_size(num: float | int | None) -> str:
    if not num:
        return "0 B"
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(num)} B"
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} TB"


def human_duration(ms: float | int) -> str:
    s = float(ms) / 1000.0
    if s < 1:
        return f"{int(ms)} ms"
    if s < 60:
        return f"{s:.1f}s"
    m, sec = divmod(int(s), 60)
    if m < 60:
        return f"{m}m {sec}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def percent(a: float, b: float) -> float:
    if not b:
        return 0.0
    return (a / b) * 100.0


def elide(text: str, limit: int = 48) -> str:
    text = str(text)
    if len(text) <= limit:
        return text
    keep = limit - 1
    return text[: keep // 2] + "…" + text[-(keep - keep // 2):]


# ---------------------------------------------------------------------------
# page ranges
# ---------------------------------------------------------------------------

_RANGE_TOKEN = re.compile(r"^\s*(\d+)\s*(?:[-–—:]\s*(\d+|end|last)\s*)?$", re.I)


def parse_page_ranges(spec: str, total: int) -> list[int]:
    """
    Parse a human page specification into a sorted list of 1-based page numbers.

    Accepts:  "1,3,7,10-15"   "1-5"   "odd"   "even"   "all"   "3-end"
              "last"          "-5" (first five)      "8-" (8 to end)
    Out-of-range values are dropped rather than raising, so a stale spec on a
    shorter document degrades gracefully.
    """
    if total <= 0:
        return []
    spec = (spec or "").strip().lower()
    if not spec or spec in ("all", "*"):
        return list(range(1, total + 1))
    if spec == "odd":
        return [i for i in range(1, total + 1) if i % 2 == 1]
    if spec == "even":
        return [i for i in range(1, total + 1) if i % 2 == 0]
    if spec in ("last", "end"):
        return [total]
    if spec == "first":
        return [1]

    pages: set[int] = set()
    for raw in re.split(r"[,\s;]+", spec):
        if not raw:
            continue
        if raw == "odd":
            pages.update(i for i in range(1, total + 1) if i % 2)
            continue
        if raw == "even":
            pages.update(i for i in range(1, total + 1) if not i % 2)
            continue
        if raw.startswith("-") and raw[1:].isdigit():          # "-5"
            end = min(int(raw[1:]), total)
            pages.update(range(1, end + 1))
            continue
        if raw.endswith("-") and raw[:-1].isdigit():           # "8-"
            start = max(1, int(raw[:-1]))
            pages.update(range(start, total + 1))
            continue
        m = _RANGE_TOKEN.match(raw)
        if not m:
            continue
        a = int(m.group(1))
        b_raw = m.group(2)
        if b_raw is None:
            if 1 <= a <= total:
                pages.add(a)
            continue
        b = total if b_raw in ("end", "last") else int(b_raw)
        lo, hi = (a, b) if a <= b else (b, a)
        lo = max(1, lo)
        hi = min(total, hi)
        if lo <= hi:
            pages.update(range(lo, hi + 1))
    return sorted(pages)


def format_page_ranges(pages: Sequence[int]) -> str:
    """Inverse of parse_page_ranges: [1,2,3,7,9,10] -> '1-3, 7, 9-10'."""
    pages = sorted(set(int(p) for p in pages))
    if not pages:
        return ""
    out: list[str] = []
    start = prev = pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = p
    out.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(out)


def chunk(seq: Sequence, size: int) -> list[list]:
    size = max(1, int(size))
    return [list(seq[i:i + size]) for i in range(0, len(seq), size)]


# ---------------------------------------------------------------------------
# output naming
# ---------------------------------------------------------------------------

_INVALID = r'<>:"/\|?*'


def sanitize_filename(name: str, fallback: str = "output") -> str:
    name = unicodedata.normalize("NFKC", str(name)).strip()
    name = "".join("_" if c in _INVALID or ord(c) < 32 else c for c in name)
    name = name.rstrip(" .")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    if name.upper().split(".")[0] in reserved:
        name = "_" + name
    return name[:180] or fallback


def unique_path(path: str | Path) -> Path:
    """
    Return a path that does not exist yet.

    document_ocr.pdf -> document_ocr_2.pdf -> document_ocr_3.pdf ...
    Never returns an existing path, so an original file can never be silently
    clobbered by the default naming scheme.
    """
    path = Path(path)
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    n = 2
    while True:
        candidate = parent / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1
        if n > 9999:
            import uuid
            return parent / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"


def suggest_output(
    source: str | Path,
    suffix: str,
    ext: str | None = None,
    out_dir: str | Path | None = None,
    unique: bool = True,
) -> Path:
    """
    Build a default output path following the app's naming convention.

        suggest_output("C:/a/report.pdf", "ocr")  ->  C:/a/report_ocr.pdf

    Honours the user's output_mode setting unless out_dir is given explicitly.
    """
    src = Path(source)
    ext = ext or src.suffix or ".pdf"
    if not ext.startswith("."):
        ext = "." + ext

    if out_dir:
        folder = Path(out_dir)
    else:
        mode = config.get("output_mode", "same_folder")
        if mode == "fixed":
            folder = Path(config.get("output_dir") or src.parent)
        else:
            folder = src.parent
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = src.parent

    if suffix and config.get("output_suffix_style", "descriptive") != "none":
        stem = f"{src.stem}_{suffix}"
    else:
        stem = src.stem

    target = folder / f"{sanitize_filename(stem)}{ext}"
    if target.resolve() == src.resolve():
        target = folder / f"{sanitize_filename(stem)}_out{ext}"
    return unique_path(target) if unique else target


def resolve_collision(path: Path) -> Path:
    """Apply the user's overwrite policy to a chosen output path."""
    policy = config.get("overwrite_policy", "unique")
    if policy == "overwrite":
        return path
    return unique_path(path)


def ensure_parent(path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def file_size(path: str | Path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def is_same_file(a: str | Path, b: str | Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# shell / explorer
# ---------------------------------------------------------------------------

def open_path(path: str | Path) -> bool:
    """Open a file with its default application."""
    p = Path(path)
    try:
        if os.name == "nt":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return True
    except Exception:
        return False


def reveal_in_explorer(path: str | Path) -> bool:
    """Open the containing folder and select the file."""
    p = Path(path)
    try:
        if os.name == "nt":
            if p.is_file():
                subprocess.Popen(["explorer", "/select,", str(p)])
            else:
                subprocess.Popen(["explorer", str(p)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p if p.is_dir() else p.parent)])
        return True
    except Exception:
        return False


def no_window_kwargs() -> dict:
    """subprocess kwargs that suppress the console flash on Windows."""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return {"startupinfo": si, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def run_quiet(cmd: list[str], timeout: int | None = None, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        **no_window_kwargs(),
        **kw,
    )


def run_cancellable(ctx, cmd: list[str], timeout: int = 600,
                    poll: float = 0.25, **kw) -> subprocess.CompletedProcess:
    """
    Run an external tool while remaining responsive to Cancel.

    ``subprocess.run`` blocks the worker thread outright, so a hung converter
    would leave the Cancel button doing nothing and the app apparently frozen
    until the timeout expired. This polls instead: on cancellation the child is
    terminated (then killed if it ignores that), and :class:`Cancelled`
    propagates as usual.
    """
    from app.core.jobs import Cancelled

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        **no_window_kwargs(),
        **kw,
    )

    waited = 0.0
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=poll)
                return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
            except subprocess.TimeoutExpired:
                pass

            waited += poll
            if ctx is not None and getattr(ctx, "cancelled", False):
                _terminate(proc)
                raise Cancelled()
            if timeout and waited >= timeout:
                _terminate(proc)
                raise subprocess.TimeoutExpired(cmd, timeout)
    except Cancelled:
        raise
    except subprocess.TimeoutExpired:
        raise
    except BaseException:
        _terminate(proc)
        raise


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=4)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------

def dedupe(seq: Iterable) -> list:
    seen, out = set(), []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def as_paths(items: Iterable) -> list[Path]:
    return [Path(i) for i in items]


def filter_by_ext(items: Iterable, exts: Sequence[str]) -> list[Path]:
    exts = tuple(e.lower() for e in exts)
    return [Path(i) for i in items if str(i).lower().endswith(exts)]


ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
         (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]


def to_roman(n: int, upper: bool = False) -> str:
    if n <= 0:
        return str(n)
    out = []
    for value, sym in ROMAN:
        while n >= value:
            out.append(sym)
            n -= value
    s = "".join(out)
    return s.upper() if upper else s


def to_letters(n: int, upper: bool = True) -> str:
    """1 -> A, 26 -> Z, 27 -> AA"""
    if n <= 0:
        return str(n)
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out if upper else out.lower()
