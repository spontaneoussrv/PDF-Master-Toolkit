"""
Batch processing engine.

Runs one operation (or a whole workflow) over many files, with per-file status,
pause, cancel, retry-failed and a report export. Designed to stay responsive
with thousands of files: results stream back one at a time through
``ctx.emit_item`` rather than accumulating into a single end-of-run payload.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Sequence

from app.core import utils, workflow
from app.core.jobs import Cancelled, JobContext, JobError


class ItemStatus(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    DONE = "Completed"
    FAILED = "Failed"
    SKIPPED = "Skipped"
    CANCELLED = "Cancelled"


STATUS_COLORS = {
    ItemStatus.PENDING: "muted",
    ItemStatus.RUNNING: "accent",
    ItemStatus.DONE: "success",
    ItemStatus.FAILED: "danger",
    ItemStatus.SKIPPED: "warning",
    ItemStatus.CANCELLED: "muted",
}


@dataclass
class BatchItem:
    path: Path
    status: ItemStatus = ItemStatus.PENDING
    progress: int = 0
    outputs: list[Path] = field(default_factory=list)
    error: str = ""
    duration_ms: int = 0
    input_size: int = 0
    output_size: int = 0
    pages: int = 0
    note: str = ""

    def __post_init__(self):
        if not self.input_size:
            self.input_size = utils.file_size(self.path)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def output_text(self) -> str:
        if not self.outputs:
            return "—"
        if len(self.outputs) == 1:
            return self.outputs[0].name
        return f"{len(self.outputs)} files"

    @property
    def saved_percent(self) -> float:
        if not self.input_size or not self.output_size:
            return 0.0
        return (1 - self.output_size / self.input_size) * 100


@dataclass
class BatchResult:
    items: list[BatchItem] = field(default_factory=list)
    started: datetime = field(default_factory=datetime.now)
    finished: datetime | None = None
    operation: str = ""
    cancelled: bool = False

    @property
    def completed(self) -> int:
        return sum(1 for i in self.items if i.status is ItemStatus.DONE)

    @property
    def failed(self) -> int:
        return sum(1 for i in self.items if i.status is ItemStatus.FAILED)

    @property
    def skipped(self) -> int:
        return sum(1 for i in self.items if i.status is ItemStatus.SKIPPED)

    @property
    def total_saved(self) -> int:
        return sum(max(0, i.input_size - i.output_size) for i in self.items
                   if i.status is ItemStatus.DONE and i.output_size)

    @property
    def all_outputs(self) -> list[Path]:
        out: list[Path] = []
        for i in self.items:
            out.extend(i.outputs)
        return out

    @property
    def message(self) -> str:
        bits = [f"{self.completed} completed"]
        if self.failed:
            bits.append(f"{self.failed} failed")
        if self.skipped:
            bits.append(f"{self.skipped} skipped")
        if self.cancelled:
            bits.append("cancelled")
        return " · ".join(bits)


@dataclass
class BatchOptions:
    out_dir: Path | None = None
    output_mode: str = "same_folder"        # same_folder | fixed | subfolder
    subfolder_name: str = "Processed"
    skip_existing: bool = False
    stop_on_error: bool = False
    name_suffix: str = ""
    password: str = ""


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

def run_batch(
    ctx: JobContext,
    files: Sequence[str | Path],
    steps: Sequence[workflow.Step],
    opts: BatchOptions | None = None,
    only_indices: Sequence[int] | None = None,
) -> BatchResult:
    """
    Process ``files`` through ``steps``.

    ``only_indices`` limits the run to a subset, which is how "Retry failed"
    re-runs just the failures without rebuilding the whole queue.
    """
    opts = opts or BatchOptions()
    problems = workflow.validate(steps)
    if problems:
        raise JobError("This batch cannot start:\n\n" + "\n".join(f"• {p}" for p in problems))

    items = [BatchItem(Path(f)) for f in files]
    result = BatchResult(items=items,
                         operation=" → ".join(s.label for s in steps if s.enabled))

    targets = list(only_indices) if only_indices is not None else list(range(len(items)))
    if not targets:
        raise JobError("No files were selected for processing.")

    ctx.set_total(len(targets), "Processing files")
    ctx.log(f"Batch started: {len(targets)} file(s) · {result.operation}")

    for n, idx in enumerate(targets, start=1):
        if not (0 <= idx < len(items)):
            continue
        item = items[idx]

        try:
            ctx.check_cancel()
        except Cancelled:
            result.cancelled = True
            for j in targets[n - 1:]:
                if items[j].status is ItemStatus.PENDING:
                    items[j].status = ItemStatus.CANCELLED
                    ctx.emit_item(items[j])
            break

        item.status = ItemStatus.RUNNING
        item.progress = 0
        item.error = ""
        item.outputs = []
        ctx.emit_item(item)
        ctx.stage(f"{item.name}")

        started = time.monotonic()
        try:
            out_dir = _resolve_out_dir(item.path, opts)
            if opts.skip_existing and _already_done(item.path, out_dir, opts):
                item.status = ItemStatus.SKIPPED
                item.note = "Output already exists"
                item.progress = 100
                ctx.emit_item(item)
                ctx.step(detail=f"{item.name} — skipped")
                continue

            sub = _SubContext(ctx, item, n - 1, len(targets))
            run = workflow.run_workflow(
                sub, item.path, steps, out_dir,
                final_name=(f"{item.path.stem}{opts.name_suffix}"
                            if opts.name_suffix else None),
                password=opts.password or None,
            )
            item.outputs = list(run.outputs)
            item.output_size = sum(utils.file_size(p) for p in run.outputs)
            item.status = ItemStatus.DONE
            item.progress = 100
            if run.warnings:
                item.note = run.warnings[0]

        except Cancelled:
            item.status = ItemStatus.CANCELLED
            result.cancelled = True
            ctx.emit_item(item)
            for j in targets[n:]:
                if items[j].status is ItemStatus.PENDING:
                    items[j].status = ItemStatus.CANCELLED
                    ctx.emit_item(items[j])
            break
        except Exception as e:
            item.status = ItemStatus.FAILED
            item.error = str(e).split("\n")[0][:300]
            item.progress = 0
            ctx.error(f"{item.name}: {item.error}")
            if opts.stop_on_error:
                ctx.emit_item(item)
                ctx.warn("Stopped after the first error, as configured.")
                break
        finally:
            item.duration_ms = int((time.monotonic() - started) * 1000)

        ctx.emit_item(item)
        ctx.step(detail=f"{item.name} — {item.status.value}")

    result.finished = datetime.now()
    ctx.progress(100, result.message)
    ctx.log(f"Batch finished: {result.message}")
    return result


class _SubContext(JobContext):
    """
    Maps one file's 0-100 progress into its slice of the overall batch bar and
    keeps the per-row progress in the table live.
    """

    def __init__(self, parent: JobContext, item: BatchItem, index: int, total: int):
        self._parent = parent
        self._item = item
        self._index = index
        self._count = max(1, total)
        self._emitter = parent._emitter                       # type: ignore[attr-defined]
        self._cancel = parent._cancel                         # type: ignore[attr-defined]
        self._pause = parent._pause                           # type: ignore[attr-defined]
        self._last_emit = 0.0
        self._total = 0
        self._done = 0
        self._stage = ""

    def progress(self, value, message: str = "") -> None:     # type: ignore[override]
        self.check_cancel()
        self._item.progress = int(utils.clamp(value, 0, 100))
        overall = (self._index + self._item.progress / 100.0) / self._count * 100.0
        self._emitter.progress.emit(int(overall),
                                    f"{self._item.name} — {message or self._stage}")
        self._emitter.item.emit(self._item)

    def _emit(self, detail: str = "", force: bool = False) -> None:   # type: ignore[override]
        self.check_cancel()
        now = time.monotonic()
        if not force and (now - self._last_emit) < 0.06:
            return
        self._last_emit = now
        pct = int(self._done * 100 / self._total) if self._total else 0
        self.progress(pct, detail or self._stage)

    def log(self, message: str, level: str = "info") -> None:  # type: ignore[override]
        self._emitter.log.emit(f"{self._item.name}: {message}", level)

    def emit_item(self, payload) -> None:                      # type: ignore[override]
        self._emitter.item.emit(payload)


def _resolve_out_dir(src: Path, opts: BatchOptions) -> Path:
    if opts.output_mode == "fixed" and opts.out_dir:
        folder = Path(opts.out_dir)
    elif opts.output_mode == "subfolder":
        folder = src.parent / (opts.subfolder_name or "Processed")
    else:
        folder = src.parent
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _already_done(src: Path, out_dir: Path, opts: BatchOptions) -> bool:
    stem = f"{src.stem}{opts.name_suffix}" if opts.name_suffix else f"{src.stem}_processed"
    return (out_dir / f"{stem}.pdf").exists()


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def export_report(result: BatchResult, output: str | Path, fmt: str = "csv") -> Path:
    out = utils.resolve_collision(Path(output))
    utils.ensure_parent(out)

    if fmt == "json":
        payload = {
            "operation": result.operation,
            "started": result.started.isoformat(timespec="seconds"),
            "finished": result.finished.isoformat(timespec="seconds") if result.finished else None,
            "completed": result.completed,
            "failed": result.failed,
            "skipped": result.skipped,
            "bytes_saved": result.total_saved,
            "items": [
                {
                    "file": str(i.path),
                    "status": i.status.value,
                    "outputs": [str(p) for p in i.outputs],
                    "input_size": i.input_size,
                    "output_size": i.output_size,
                    "duration_ms": i.duration_ms,
                    "error": i.error,
                    "note": i.note,
                }
                for i in result.items
            ],
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return out

    if fmt == "txt":
        lines = [
            "BATCH PROCESSING REPORT",
            "=" * 62,
            f"Operation : {result.operation}",
            f"Started   : {result.started:%d %b %Y %H:%M:%S}",
            f"Finished  : {result.finished:%d %b %Y %H:%M:%S}" if result.finished else "",
            f"Summary   : {result.message}",
            f"Saved     : {utils.human_size(result.total_saved)}",
            "",
        ]
        for i in result.items:
            lines.append(f"[{i.status.value:<10}] {i.name}")
            if i.outputs:
                for o in i.outputs:
                    lines.append(f"              → {o}")
            if i.error:
                lines.append(f"              ! {i.error}")
        out.write_text("\n".join(l for l in lines if l is not None), encoding="utf-8")
        return out

    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Filename", "Status", "Input size", "Output size", "Saved %",
                    "Pages", "Duration", "Output", "Error", "Note"])
        for i in result.items:
            w.writerow([
                i.name, i.status.value,
                utils.human_size(i.input_size),
                utils.human_size(i.output_size) if i.output_size else "",
                f"{i.saved_percent:.1f}" if i.output_size else "",
                i.pages or "",
                utils.human_duration(i.duration_ms),
                "; ".join(str(p) for p in i.outputs),
                i.error, i.note,
            ])
    return out
