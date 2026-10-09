"""Batch Processing — one operation (or a whole workflow) across many files."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel,
                               QProgressBar, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from app import db
from app.core import batch as batch_mod
from app.core import utils, workflow
from app.ui import components as C
from app.ui.pages.base import ToolPage
from app.ui.pages.workflow_page import StepEditor

ALL_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp",
           ".docx", ".xlsx", ".xls", ".csv")

STATUS_COLOR = {
    batch_mod.ItemStatus.DONE: QColor(233, 248, 240),
    batch_mod.ItemStatus.FAILED: QColor(253, 236, 234),
    batch_mod.ItemStatus.SKIPPED: QColor(255, 246, 230),
    batch_mod.ItemStatus.RUNNING: QColor(233, 240, 255),
}


class BatchPage(ToolPage):
    TITLE = "Batch Processing"
    SUBTITLE = "Run one job across hundreds of files"
    ACTION = "Start batch"
    SHOW_PAUSE = True
    HISTORY_MODULE = "Batch"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self._rows: dict[str, int] = {}
        self._result: batch_mod.BatchResult | None = None
        self._items: list[batch_mod.BatchItem] = []

    def build(self) -> None:
        files_card = C.Card("Files to process",
                            "Add files or a whole folder. Thousands are fine — they "
                            "are processed one at a time.")
        self.files = C.FileListWidget(ALL_EXT, allow_duplicates=False)
        self.files.changed.connect(self._on_files_changed)
        files_card.add(self.files)
        self.scroll.add(files_card)

        op_card = C.Card("What to do")
        self.op = QComboBox()
        for category, ops in workflow.operations_by_category().items():
            for o in ops:
                self.op.addItem(f"{category}  ·  {o.label}", o.key)
        self.op.addItem("Saved workflow…", "__workflow__")
        self.op.currentIndexChanged.connect(self._on_operation)
        op_card.add(C.FormRow("Operation", self.op))

        self.op_desc = C.muted("")
        op_card.add(self.op_desc)

        self.workflow_combo = QComboBox()
        self.workflow_row = C.FormRow("Workflow", self.workflow_combo)
        self.workflow_row.setVisible(False)
        op_card.add(self.workflow_row)

        self.editor_holder = QWidget()
        self.editor_layout = QVBoxLayout(self.editor_holder)
        self.editor_layout.setContentsMargins(0, 6, 0, 0)
        op_card.add(self.editor_holder)
        self.scroll.add(op_card)

        out_card = C.Card("Where the results go")
        self.out_mode = C.SegmentedControl([
            ("same_folder", "Beside each original"),
            ("subfolder", "In a subfolder"),
            ("fixed", "One chosen folder"),
        ], "subfolder")
        self.out_mode.changed.connect(self._on_out_mode)
        out_card.add(C.FormRow("Location", self.out_mode))

        from PySide6.QtWidgets import QLineEdit
        self.subfolder = QLineEdit("Processed")
        self.subfolder_row = C.FormRow("Subfolder name", self.subfolder)
        out_card.add(self.subfolder_row)

        self.out_dir = C.PathPicker("folder", placeholder="Choose an output folder")
        self.out_dir_row = C.FormRow("Folder", self.out_dir)
        self.out_dir_row.setVisible(False)
        out_card.add(self.out_dir_row)

        self.suffix = QLineEdit("_processed")
        out_card.add(C.FormRow("Add to each file name", self.suffix))

        self.skip_existing = C.check("Skip files that already have an output", False)
        self.stop_on_error = C.check("Stop at the first failure", False,
                                     "Off by default — one bad file should not halt "
                                     "the whole run.")
        out_card.add(self.skip_existing)
        out_card.add(self.stop_on_error)
        self.scroll.add(out_card)

        self.add_run_section("Start batch")

        self.queue_card = C.Card("Queue")
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["File", "Status", "Progress", "Output", "Time", "Error"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setMinimumHeight(300)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.queue_card.add(self.table)

        row = QHBoxLayout()
        self.retry_btn = QPushButton("Retry failed")
        self.retry_btn.setObjectName("ChipButton")
        self.retry_btn.clicked.connect(self._retry_failed)
        self.open_btn = QPushButton("Open output folder")
        self.open_btn.setObjectName("ChipButton")
        self.open_btn.clicked.connect(self._open_output)
        self.report_btn = QPushButton("Export report")
        self.report_btn.setObjectName("ChipButton")
        self.report_btn.clicked.connect(self._export_report)
        for b in (self.retry_btn, self.open_btn, self.report_btn):
            row.addWidget(b)
        row.addStretch(1)
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("Subtle")
        row.addWidget(self.summary_label)
        self.queue_card.add_layout(row)
        self.queue_card.setVisible(False)
        self.scroll.add(self.queue_card)

        self._on_operation()
        self._on_out_mode(self.out_mode.value())
        self.run_bar.set_enabled(False)

    # -- operation ---------------------------------------------------------
    def _on_operation(self) -> None:
        key = self.op.currentData()
        while self.editor_layout.count():
            item = self.editor_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if key == "__workflow__":
            self.workflow_row.setVisible(True)
            self.workflow_combo.clear()
            for wf in db.get_workflows():
                self.workflow_combo.addItem(
                    f"{wf['name']}  ({len(wf['steps'])} steps)", wf["name"])
            for name in workflow.PRESET_WORKFLOWS:
                self.workflow_combo.addItem(f"{name}  (built in)", f"preset:{name}")
            self.op_desc.setText(
                "Runs a saved chain of operations on each file. Build one on the "
                "Workflow Builder screen.")
            return

        self.workflow_row.setVisible(False)
        op = workflow.OPERATIONS.get(key)
        if op is None:
            return
        self.op_desc.setText(op.description)
        self.editor = StepEditor(workflow.Step(op.key, op.defaults()))
        self.editor_layout.addWidget(self.editor)

    def _on_out_mode(self, value: str) -> None:
        self.subfolder_row.setVisible(value == "subfolder")
        self.out_dir_row.setVisible(value == "fixed")

    def _on_files_changed(self) -> None:
        n = self.files.count()
        if self.run_bar:
            self.run_bar.set_enabled(n > 0)
        self.set_status(f"{n} file(s) queued" if n else "Add some files")

    def handle_files(self, paths: Sequence[str]) -> None:
        wanted = [p for p in paths if Path(p).suffix.lower() in ALL_EXT]
        if wanted:
            self.files.add_files(wanted)

    # -- run ---------------------------------------------------------------
    def _steps(self) -> list[workflow.Step]:
        key = self.op.currentData()
        if key == "__workflow__":
            name = self.workflow_combo.currentData()
            if not name:
                return []
            if str(name).startswith("preset:"):
                return workflow.preset_steps(str(name)[7:])
            for wf in db.get_workflows():
                if wf["name"] == name:
                    return [workflow.Step.from_dict(s) for s in wf["steps"]]
            return []
        return [self.editor.step()]

    def validate(self) -> str:
        if self.files.count() == 0:
            return "Add at least one file."
        steps = self._steps()
        if not steps:
            return "Choose an operation, or pick a saved workflow."
        problems = workflow.validate(steps)
        if problems:
            return problems[0]
        if self.out_mode.value() == "fixed" and not self.out_dir.path():
            return "Choose the output folder."
        return ""

    def start(self) -> None:
        files = self.files.paths()
        self._items = [batch_mod.BatchItem(Path(f)) for f in files]
        self._build_table(self._items)
        self.queue_card.setVisible(True)

        opts = batch_mod.BatchOptions(
            out_dir=self.out_dir.path(),
            output_mode=self.out_mode.value(),
            subfolder_name=self.subfolder.text().strip() or "Processed",
            skip_existing=self.skip_existing.isChecked(),
            stop_on_error=self.stop_on_error.isChecked(),
            name_suffix=self.suffix.text().strip(),
        )
        self._retry_indices = None
        self.run_job(batch_mod.run_batch, files, self._steps(), opts, None,
                     on_item=self._on_item,
                     name=f"Batch: {len(files)} file(s)")

    def _retry_failed(self) -> None:
        if not self._result:
            return
        failed = [i for i, item in enumerate(self._result.items)
                  if item.status is batch_mod.ItemStatus.FAILED]
        if not failed:
            self.toast("Nothing failed", "success")
            return
        opts = batch_mod.BatchOptions(
            out_dir=self.out_dir.path(),
            output_mode=self.out_mode.value(),
            subfolder_name=self.subfolder.text().strip() or "Processed",
            skip_existing=False,
            stop_on_error=False,
            name_suffix=self.suffix.text().strip(),
        )
        self.run_job(batch_mod.run_batch, self.files.paths(), self._steps(), opts,
                     failed, on_item=self._on_item,
                     name=f"Retrying {len(failed)} file(s)")

    # -- table -------------------------------------------------------------
    def _build_table(self, items) -> None:
        self.table.setRowCount(len(items))
        self._rows = {}
        for i, item in enumerate(items):
            self._rows[str(item.path)] = i
            self.table.setItem(i, 0, QTableWidgetItem(item.name))
            self.table.item(i, 0).setToolTip(str(item.path))
            self.table.setItem(i, 1, QTableWidgetItem(item.status.value))
            bar = QProgressBar()
            bar.setObjectName("TableProgress")
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(False)
            self.table.setCellWidget(i, 2, bar)
            for col in (3, 4, 5):
                self.table.setItem(i, col, QTableWidgetItem(""))
        self.table.resizeColumnToContents(1)

    def _on_item(self, item) -> None:
        row = self._rows.get(str(item.path))
        if row is None:
            return
        status_item = self.table.item(row, 1)
        if status_item:
            status_item.setText(item.status.value)
        color = STATUS_COLOR.get(item.status)
        for col in range(self.table.columnCount()):
            cell = self.table.item(row, col)
            if cell is not None and color is not None:
                cell.setBackground(color)

        bar = self.table.cellWidget(row, 2)
        if bar is not None:
            bar.setValue(int(item.progress))
            bar.setObjectName("ProgressSuccess" if item.status is batch_mod.ItemStatus.DONE
                              else "ProgressDanger" if item.status is batch_mod.ItemStatus.FAILED
                              else "TableProgress")
            bar.style().unpolish(bar)
            bar.style().polish(bar)

        if self.table.item(row, 3):
            self.table.item(row, 3).setText(item.output_text)
        if self.table.item(row, 4):
            self.table.item(row, 4).setText(
                utils.human_duration(item.duration_ms) if item.duration_ms else "")
        if self.table.item(row, 5):
            self.table.item(row, 5).setText(item.error or item.note)
            self.table.item(row, 5).setToolTip(item.error or item.note)

    # -- results -----------------------------------------------------------
    def on_success(self, result: batch_mod.BatchResult) -> None:
        self._result = result
        saved = utils.human_size(result.total_saved) if result.total_saved else ""
        detail = []
        if saved:
            detail.append(f"Total space saved: {saved}")
        if result.failed:
            detail.append(f"{result.failed} file(s) failed — use “Retry failed” after "
                          f"checking the errors in the table.")

        self.summary_label.setText(result.message)
        self.show_result(result.message, result.all_outputs[:6], "\n".join(detail))
        self.record("Batch", None, None, 0,
                    f"{result.completed} completed, {result.failed} failed")
        db.bump_stat("batch_jobs")
        db.bump_stat("pdfs_processed", result.completed)
        if result.total_saved:
            db.bump_stat("bytes_saved", result.total_saved)
        self.toast(result.message,
                   "success" if not result.failed else "warning")

    def on_cancelled(self) -> None:
        self.summary_label.setText("Cancelled — files already finished are kept.")

    def _open_output(self) -> None:
        if self._result and self._result.all_outputs:
            utils.reveal_in_explorer(self._result.all_outputs[0])
        elif self.out_dir.path():
            utils.open_path(self.out_dir.path())
        else:
            self.toast("Nothing has been produced yet", "warning")

    def _export_report(self) -> None:
        if not self._result:
            self.toast("Run a batch first", "warning")
            return
        from PySide6.QtWidgets import QFileDialog
        path, selected = QFileDialog.getSaveFileName(
            self, "Export processing report",
            str(Path.home() / "batch_report.csv"),
            "CSV (*.csv);;JSON (*.json);;Text (*.txt)")
        if not path:
            return
        fmt = Path(path).suffix.lstrip(".").lower() or "csv"
        try:
            out = batch_mod.export_report(self._result, path, fmt)
        except Exception as e:
            self.report_failure(str(e))
            return
        self.toast(f"Report saved as {out.name}")
        utils.reveal_in_explorer(out)
