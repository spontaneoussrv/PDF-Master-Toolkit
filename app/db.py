"""
SQLite storage: settings, job history, statistics, saved signatures, workflows.

PRIVACY: history stores file *paths and operation names only*. Document text,
page content, passwords and extracted data are never written to this database.
History can be disabled entirely (Settings -> Privacy), in which case every
write in this module becomes a no-op.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from app import paths

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    operation   TEXT    NOT NULL,
    module      TEXT    NOT NULL,
    input_name  TEXT,
    input_path  TEXT,
    output_name TEXT,
    output_path TEXT,
    input_size  INTEGER DEFAULT 0,
    output_size INTEGER DEFAULT 0,
    pages       INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    status      TEXT    DEFAULT 'success',
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_ts ON history(ts DESC);
CREATE INDEX IF NOT EXISTS idx_history_module ON history(module);

CREATE TABLE IF NOT EXISTS stats (
    key   TEXT PRIMARY KEY,
    value REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS signatures (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    kind    TEXT NOT NULL,          -- drawn | typed | image
    file    TEXT NOT NULL,          -- PNG in %LOCALAPPDATA%/.../signatures
    created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflows (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT UNIQUE NOT NULL,
    steps   TEXT NOT NULL,          -- JSON list of {op, params}
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recent_files (
    path      TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    last_op   TEXT,
    last_used TEXT NOT NULL,
    output    TEXT
);
"""


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------

def connect() -> sqlite3.Connection:
    global _CONN
    with _LOCK:
        if _CONN is None:
            _CONN = sqlite3.connect(
                str(paths.database_path()),
                check_same_thread=False,
                timeout=15.0,
            )
            _CONN.row_factory = sqlite3.Row
            _CONN.execute("PRAGMA journal_mode=WAL")
            _CONN.execute("PRAGMA synchronous=NORMAL")
            _CONN.executescript(_SCHEMA)
            _CONN.commit()
            _migrate(_CONN)
        return _CONN


def _migrate(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT value FROM settings WHERE key='schema_version'")
    row = cur.fetchone()
    current = int(row["value"]) if row else 0
    if current < SCHEMA_VERSION:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()


@contextmanager
def cursor():
    conn = connect()
    with _LOCK:
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def close() -> None:
    global _CONN
    with _LOCK:
        if _CONN is not None:
            try:
                _CONN.commit()
                _CONN.close()
            except Exception:
                pass
            _CONN = None


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

def get_setting(key: str, default: Any = None) -> Any:
    try:
        with cursor() as c:
            c.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = c.fetchone()
    except Exception:
        return default
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


def set_setting(key: str, value: Any) -> None:
    try:
        with cursor() as c:
            c.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
                (key, json.dumps(value)),
            )
    except Exception:
        pass


def all_settings() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        with cursor() as c:
            c.execute("SELECT key, value FROM settings")
            for row in c.fetchall():
                try:
                    out[row["key"]] = json.loads(row["value"])
                except Exception:
                    out[row["key"]] = row["value"]
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

def history_enabled() -> bool:
    return bool(get_setting("history_enabled", True))


def add_history(
    operation: str,
    module: str,
    input_path: str | Path | None = None,
    output_path: str | Path | None = None,
    input_size: int = 0,
    output_size: int = 0,
    pages: int = 0,
    duration_ms: int = 0,
    status: str = "success",
    detail: str = "",
) -> None:
    """Record one completed operation. No-op when history is disabled."""
    if not history_enabled():
        return
    ip = Path(input_path) if input_path else None
    op_ = Path(output_path) if output_path else None
    try:
        with cursor() as c:
            c.execute(
                """INSERT INTO history
                   (ts, operation, module, input_name, input_path, output_name,
                    output_path, input_size, output_size, pages, duration_ms,
                    status, detail)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    operation,
                    module,
                    ip.name if ip else None,
                    str(ip) if ip else None,
                    op_.name if op_ else None,
                    str(op_) if op_ else None,
                    int(input_size or 0),
                    int(output_size or 0),
                    int(pages or 0),
                    int(duration_ms or 0),
                    status,
                    (detail or "")[:400],
                ),
            )
        if ip:
            touch_recent(ip, operation, op_)
    except Exception:
        pass


def get_history(limit: int = 200, module: str | None = None, search: str = "") -> list[sqlite3.Row]:
    q = "SELECT * FROM history WHERE 1=1"
    args: list[Any] = []
    if module:
        q += " AND module=?"
        args.append(module)
    if search:
        q += " AND (input_name LIKE ? OR output_name LIKE ? OR operation LIKE ?)"
        like = f"%{search}%"
        args += [like, like, like]
    q += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    try:
        with cursor() as c:
            c.execute(q, args)
            return c.fetchall()
    except Exception:
        return []


def clear_history() -> None:
    try:
        with cursor() as c:
            c.execute("DELETE FROM history")
            c.execute("DELETE FROM recent_files")
    except Exception:
        pass


def prune_history(days: int) -> int:
    if days <= 0:
        return 0
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    try:
        with cursor() as c:
            c.execute("DELETE FROM history WHERE ts < ?", (cutoff,))
            return c.rowcount
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# recent files
# ---------------------------------------------------------------------------

def touch_recent(path: Path, operation: str, output: Path | None = None) -> None:
    if not history_enabled():
        return
    try:
        with cursor() as c:
            c.execute(
                """INSERT INTO recent_files(path, name, last_op, last_used, output)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET
                     last_op=excluded.last_op,
                     last_used=excluded.last_used,
                     output=excluded.output""",
                (
                    str(path),
                    path.name,
                    operation,
                    datetime.now().isoformat(timespec="seconds"),
                    str(output) if output else None,
                ),
            )
            c.execute(
                """DELETE FROM recent_files WHERE path NOT IN
                   (SELECT path FROM recent_files ORDER BY last_used DESC LIMIT 60)"""
            )
    except Exception:
        pass


def get_recent(limit: int = 12) -> list[sqlite3.Row]:
    try:
        with cursor() as c:
            c.execute("SELECT * FROM recent_files ORDER BY last_used DESC LIMIT ?", (int(limit),))
            return c.fetchall()
    except Exception:
        return []


def clear_recent() -> None:
    try:
        with cursor() as c:
            c.execute("DELETE FROM recent_files")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

STAT_KEYS = (
    "pdfs_processed",
    "ocr_documents",
    "ocr_pages",
    "files_converted",
    "bytes_saved",
    "pages_processed",
    "batch_jobs",
)


def bump_stat(key: str, amount: float = 1) -> None:
    try:
        with cursor() as c:
            c.execute(
                """INSERT INTO stats(key, value) VALUES(?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = value + excluded.value""",
                (key, float(amount)),
            )
    except Exception:
        pass


def get_stat(key: str) -> float:
    try:
        with cursor() as c:
            c.execute("SELECT value FROM stats WHERE key=?", (key,))
            row = c.fetchone()
            return float(row["value"]) if row else 0.0
    except Exception:
        return 0.0


def get_stats() -> dict[str, float]:
    out = {k: 0.0 for k in STAT_KEYS}
    try:
        with cursor() as c:
            c.execute("SELECT key, value FROM stats")
            for row in c.fetchall():
                out[row["key"]] = float(row["value"])
    except Exception:
        pass
    return out


def reset_stats() -> None:
    try:
        with cursor() as c:
            c.execute("DELETE FROM stats")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# signatures
# ---------------------------------------------------------------------------

def add_signature(name: str, kind: str, file: str | Path) -> int:
    with cursor() as c:
        c.execute(
            "INSERT INTO signatures(name, kind, file, created) VALUES(?,?,?,?)",
            (name, kind, str(file), datetime.now().isoformat(timespec="seconds")),
        )
        return int(c.lastrowid)


def get_signatures() -> list[sqlite3.Row]:
    try:
        with cursor() as c:
            c.execute("SELECT * FROM signatures ORDER BY id DESC")
            return c.fetchall()
    except Exception:
        return []


def delete_signature(sig_id: int) -> None:
    try:
        with cursor() as c:
            c.execute("SELECT file FROM signatures WHERE id=?", (sig_id,))
            row = c.fetchone()
            if row:
                try:
                    Path(row["file"]).unlink(missing_ok=True)
                except OSError:
                    pass
            c.execute("DELETE FROM signatures WHERE id=?", (sig_id,))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# workflows
# ---------------------------------------------------------------------------

def save_workflow(name: str, steps: Iterable[dict]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with cursor() as c:
        c.execute(
            """INSERT INTO workflows(name, steps, created, updated) VALUES(?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET steps=excluded.steps, updated=excluded.updated""",
            (name, json.dumps(list(steps)), now, now),
        )


def get_workflows() -> list[dict]:
    out = []
    try:
        with cursor() as c:
            c.execute("SELECT * FROM workflows ORDER BY name")
            for row in c.fetchall():
                try:
                    steps = json.loads(row["steps"])
                except Exception:
                    steps = []
                out.append({"id": row["id"], "name": row["name"], "steps": steps,
                            "updated": row["updated"]})
    except Exception:
        pass
    return out


def delete_workflow(name: str) -> None:
    try:
        with cursor() as c:
            c.execute("DELETE FROM workflows WHERE name=?", (name,))
    except Exception:
        pass
