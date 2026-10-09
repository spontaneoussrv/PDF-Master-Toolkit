"""
Workflow Builder.

Chains several tools into one saved recipe:

    Scanned PDF → OCR → Compress → Page numbers → Watermark → Protect → Save

:class:`StepEditor` builds its controls from the parameter descriptors in
:mod:`app.core.workflow`, so a new chainable operation appears here — and in
Batch Processing — with no UI work at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QVBoxLayout,
                               QWidget)

from app import db
from app.core import utils, workflow
from app.ui import components as C
from app.ui.pages.base import ToolPage


# ---------------------------------------------------------------------------
# parameter editor
# ---------------------------------------------------------------------------

class StepEditor(QFrame):
    """Auto-generated controls for one workflow step."""

    changed = Signal()

    def __init__(self, step: workflow.Step, parent=None):
        super().__init__(parent)
        self.setObjectName("CardFlat")
        self._step = step
        self._widgets: dict[str, QWidget] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        op = step.operation
        if op is None:
            lay.addWidget(QLabel(f"Unknown operation: {step.op}"))
            return

        title = QLabel(f"{op.icon}   {op.label}")
        title.setObjectName("CardTitle")
        lay.addWidget(title)
        desc = QLabel(op.description)
        desc.setObjectName("Subtle")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        if not op.params:
            hint = QLabel("This step has no settings.")
            hint.setObjectName("Subtle")
            lay.addWidget(hint)
            return

        for p in op.params:
            value = step.params.get(p.key, p.default)
            widget = self._make_widget(p, value)
            self._widgets[p.key] = widget
            label = p.label + ("" if p.optional else "  *")
            lay.addWidget(C.FormRow(label, widget, p.hint, label_width=190))

    def _make_widget(self, p: workflow.Param, value) -> QWidget:
        if p.kind == "bool":
            w = C.check("", bool(value))
            w.toggled.connect(self.changed.emit)
            return w
        if p.kind == "int":
            w = C.spin(int(value or 0), int(p.minimum), int(p.maximum))
            w.valueChanged.connect(self.changed.emit)
            return w
        if p.kind == "float":
            w = C.dspin(float(value or 0), float(p.minimum), float(p.maximum),
                        step=0.05, decimals=2)
            w.valueChanged.connect(self.changed.emit)
            return w
        if p.kind == "choice":
            w = C.combo(p.choices, value)
            w.currentIndexChanged.connect(self.changed.emit)
            return w
        if p.kind == "color":
            w = C.ColorPicker(str(value or "#000000"))
            w.color_changed.connect(lambda _: self.changed.emit())
            return w
        if p.kind == "path":
            w = C.PathPicker("open", default=str(value or ""))
            w.changed.connect(lambda _: self.changed.emit())
            return w
        if p.kind == "pages":
            w = C.PageRangeEdit(0, default=str(value or "all"))
            w.textChanged.connect(self.changed.emit)
            return w
        w = QLineEdit(str(value if value is not None else ""))
        w.setPlaceholderText(p.hint)
        w.textChanged.connect(self.changed.emit)
        return w

    def step(self) -> workflow.Step:
        params = {}
        for key, w in self._widgets.items():
            if isinstance(w, QLineEdit):
                params[key] = w.text()
            elif isinstance(w, QComboBox):
                params[key] = w.currentData()
            elif isinstance(w, C.ColorPicker):
                params[key] = w.color()
            elif isinstance(w, C.PathPicker):
                params[key] = w.text()
            elif hasattr(w, "isChecked"):
                params[key] = w.isChecked()
            elif hasattr(w, "value"):
                params[key] = w.value()
        return workflow.Step(self._step.op, params, self._step.enabled)


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------

class WorkflowPage(ToolPage):
    TITLE = "Workflow Builder"
    SUBTITLE = "Chain several tools into one saved recipe"
    ACTION = "Run workflow"
    HISTORY_MODULE = "Workflow"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self.steps: list[workflow.Step] = []
        self._editor: StepEditor | None = None

    def build(self) -> None:
        self._root.removeWidget(self.scroll)
        self.scroll.setParent(None)

        outer = QWidget()
        lay = QHBoxLayout(outer)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)

        # ---- left: the chain ---------------------------------------------
        left = C.ScrollArea(margins=(0, 0, 0, 0), spacing=14)
        left.setMinimumWidth(400)
        left.setMaximumWidth(520)

        saved_card = C.Card("Saved workflows")
        self.saved = QComboBox()
        saved_card.add(self.saved)
        srow = QHBoxLayout()
        for text, slot in [("Load", self._load_saved), ("Save", self._save_workflow),
                           ("Delete", self._delete_workflow)]:
            b = QPushButton(text)
            b.setObjectName("ChipButton")
            b.clicked.connect(slot)
            srow.addWidget(b)
        srow.addStretch(1)
        saved_card.add_layout(srow)
        left.add(saved_card)

        steps_card = C.Card("Steps",
                            "They run top to bottom, each on the output of the last. "
                            "Drag to reorder.")
        self.step_list = QListWidget()
        self.step_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.step_list.setMinimumHeight(240)
        self.step_list.currentRowChanged.connect(self._on_step_selected)
        self.step_list.model().rowsMoved.connect(self._on_reorder)
        self.step_list.itemChanged.connect(self._on_item_changed)
        steps_card.add(self.step_list)

        add_row = QHBoxLayout()
        self.add_combo = QComboBox()
        for category, ops in workflow.operations_by_category().items():
            for o in ops:
                self.add_combo.addItem(f"{category}  ·  {o.label}", o.key)
        add_row.addWidget(self.add_combo, 1)
        add_btn = QPushButton("Add step")
        add_btn.setObjectName("Primary")
        add_btn.clicked.connect(self._add_step)
        add_row.addWidget(add_btn)
        steps_card.add_layout(add_row)

        tools = QHBoxLayout()
        for text, slot in [("Move up", lambda: self._move(-1)),
                           ("Move down", lambda: self._move(1)),
                           ("Remove", self._remove_step),
                           ("Clear", self._clear_steps)]:
            b = QPushButton(text)
            b.setObjectName("ChipButton")
            b.clicked.connect(slot)
            tools.addWidget(b)
        tools.addStretch(1)
        steps_card.add_layout(tools)
        left.add(steps_card)

        preset_card = C.Card("Start from a template")
        for name in workflow.PRESET_WORKFLOWS:
            b = QPushButton(name)
            b.setObjectName("QuickAction")
            steps = workflow.PRESET_WORKFLOWS[name]
            b.setToolTip(" → ".join(s.label for s in steps))
            b.clicked.connect(lambda _=False, n=name: self._load_preset(n))
            preset_card.add(b)
        left.add(preset_card)
        left.add_stretch()
        lay.addWidget(left, 2)

        # ---- right: step settings + run ----------------------------------
        right = C.ScrollArea(margins=(0, 0, 0, 0), spacing=14)

        self.settings_card = C.Card("Step settings",
                                    "Select a step on the left to configure it.")
        self.settings_holder = QWidget()
        self.settings_layout = QVBoxLayout(self.settings_holder)
        self.settings_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_card.add(self.settings_holder)
        right.add(self.settings_card)

        files_card = C.Card("Files to run it on")
        self.files = C.FileListWidget((".pdf",), allow_duplicates=False)
        self.files.changed.connect(self._validate_live)
        files_card.add(self.files)
        right.add(files_card)

        out_card = C.Card("Output")
        self.out_dir = C.PathPicker("folder", placeholder="Beside each original file")
        out_card.add(C.FormRow("Save to", self.out_dir))
        right.add(out_card)

        self.problems = C.Banner("", "warning")
        self.problems.setVisible(False)
        right.add(self.problems)

        self.result_panel = C.ResultPanel()
        right.add(self.result_panel)

        log_card = C.Card("Activity", flat=True, shadow=False)
        self.log = C.LogView()
        log_card.add(self.log)
        log_card.setVisible(False)
        self._log_card = log_card
        right.add(log_card)

        self.run_bar = C.RunBar("Run workflow")
        self.run_bar.run_clicked.connect(self._on_run_clicked)
        self.run_bar.cancel_clicked.connect(self.cancel)
        right.add(self.run_bar)
        right.add_stretch()
        lay.addWidget(right, 3)

        self._root.addWidget(outer, 1)
        self._refresh_saved()
        self._refresh_steps()

    # -- step list ---------------------------------------------------------
    def _refresh_steps(self) -> None:
        self.step_list.blockSignals(True)
        self.step_list.clear()
        for i, step in enumerate(self.steps):
            op = step.operation
            icon = op.icon if op else "?"
            item = QListWidgetItem(f"{i + 1}.  {icon}   {step.label}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if step.enabled else Qt.Unchecked)
            if op:
                item.setToolTip(op.description)
            self.step_list.addItem(item)
        self.step_list.blockSignals(False)
        self._validate_live()

    def _add_step(self) -> None:
        key = self.add_combo.currentData()
        op = workflow.OPERATIONS.get(key)
        if op is None:
            return
        self.steps.append(workflow.Step(key, op.defaults()))
        self._refresh_steps()
        self.step_list.setCurrentRow(len(self.steps) - 1)

    def _remove_step(self) -> None:
        row = self.step_list.currentRow()
        if 0 <= row < len(self.steps):
            self._commit_editor()
            del self.steps[row]
            self._refresh_steps()
            self._clear_editor()

    def _clear_steps(self) -> None:
        if self.steps and not self.confirm("Clear the workflow?",
                                           "All steps will be removed."):
            return
        self.steps.clear()
        self._refresh_steps()
        self._clear_editor()

    def _move(self, delta: int) -> None:
        row = self.step_list.currentRow()
        new = row + delta
        if not (0 <= row < len(self.steps) and 0 <= new < len(self.steps)):
            return
        self._commit_editor()
        self.steps.insert(new, self.steps.pop(row))
        self._refresh_steps()
        self.step_list.setCurrentRow(new)

    def _on_reorder(self, *_args) -> None:
        order = []
        for row in range(self.step_list.count()):
            text = self.step_list.item(row).text()
            try:
                original = int(text.split(".")[0]) - 1
            except ValueError:
                return
            order.append(original)
        if sorted(order) != list(range(len(self.steps))):
            return
        self.steps = [self.steps[i] for i in order]
        self._refresh_steps()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        row = self.step_list.row(item)
        if 0 <= row < len(self.steps):
            self.steps[row].enabled = item.checkState() == Qt.Checked
            self._validate_live()

    def _on_step_selected(self, row: int) -> None:
        self._commit_editor()
        self._clear_editor()
        if not (0 <= row < len(self.steps)):
            self.settings_card.set_subtitle("Select a step on the left to configure it.")
            return
        step = self.steps[row]
        self.settings_card.set_subtitle(f"Step {row + 1} of {len(self.steps)}")
        self._editor = StepEditor(step)
        self._editor.changed.connect(self._commit_editor)
        self.settings_layout.addWidget(self._editor)

    def _clear_editor(self) -> None:
        while self.settings_layout.count():
            item = self.settings_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._editor = None

    def _commit_editor(self) -> None:
        if self._editor is None:
            return
        row = self.step_list.currentRow()
        if 0 <= row < len(self.steps):
            self.steps[row] = self._editor.step()
        self._validate_live()

    # -- saved workflows ---------------------------------------------------
    def _refresh_saved(self) -> None:
        self.saved.clear()
        for wf in db.get_workflows():
            self.saved.addItem(f"{wf['name']}  ({len(wf['steps'])} steps)", wf["name"])
        if self.saved.count() == 0:
            self.saved.addItem("No saved workflows yet", None)

    def _load_saved(self) -> None:
        name = self.saved.currentData()
        if not name:
            return
        for wf in db.get_workflows():
            if wf["name"] == name:
                self.steps = [workflow.Step.from_dict(s) for s in wf["steps"]]
                self._refresh_steps()
                self._clear_editor()
                self.toast(f"Loaded “{name}”")
                return

    def _save_workflow(self) -> None:
        self._commit_editor()
        if not self.steps:
            self.toast("Add some steps first", "warning")
            return
        name, ok = QInputDialog.getText(self, "Save workflow", "Name this workflow:")
        if not ok or not name.strip():
            return
        try:
            db.save_workflow(name.strip(), [s.to_dict() for s in self.steps])
        except Exception as e:
            self.report_failure(f"Could not save the workflow: {e}")
            return
        self._refresh_saved()
        self.toast(f"Saved “{name.strip()}”")

    def _delete_workflow(self) -> None:
        name = self.saved.currentData()
        if not name:
            return
        if not self.confirm("Delete this workflow?", f"“{name}” will be removed.",
                            destructive=True):
            return
        db.delete_workflow(name)
        self._refresh_saved()
        self.toast("Workflow deleted")

    def _load_preset(self, name: str) -> None:
        self.steps = workflow.preset_steps(name)
        self._refresh_steps()
        self._clear_editor()
        self.toast(f"Loaded the “{name}” template")

    # -- run ---------------------------------------------------------------
    def _validate_live(self) -> None:
        problems = workflow.validate(self.steps) if self.steps else []
        if problems:
            self.problems.set_text("\n".join(f"• {p}" for p in problems))
            self.problems.setVisible(True)
        else:
            self.problems.setVisible(False)
        if self.run_bar:
            self.run_bar.set_enabled(bool(self.steps) and not problems
                                     and self.files.count() > 0)
            if self.files.count() > 1:
                self.run_bar.set_action_text(
                    f"Run on {self.files.count()} files")
            else:
                self.run_bar.set_action_text("Run workflow")

    def handle_files(self, paths: Sequence[str]) -> None:
        pdfs = [p for p in paths if str(p).lower().endswith(".pdf")]
        if pdfs:
            self.files.add_files(pdfs)

    def validate(self) -> str:
        self._commit_editor()
        if not self.steps:
            return "Add at least one step."
        problems = workflow.validate(self.steps)
        if problems:
            return problems[0]
        if self.files.count() == 0:
            return "Add at least one PDF to run it on."
        return ""

    def start(self) -> None:
        files = [Path(p) for p in self.files.paths()]
        if len(files) > 1:
            # more than one file is a batch; reuse the batch engine
            from app.core import batch as batch_mod
            opts = batch_mod.BatchOptions(
                out_dir=self.out_dir.path(),
                output_mode="fixed" if self.out_dir.path() else "same_folder",
                name_suffix="_processed",
            )
            self.run_job(batch_mod.run_batch, files, self.steps, opts,
                         name=f"Workflow on {len(files)} files")
            return

        self.run_job(workflow.run_workflow, files[0], self.steps,
                     self.out_dir.path(), None, None,
                     name=f"Workflow on {files[0].name}")

    def on_success(self, result) -> None:
        from app.core import batch as batch_mod
        if isinstance(result, batch_mod.BatchResult):
            self.show_result(result.message, result.all_outputs[:6])
            db.bump_stat("batch_jobs")
            db.bump_stat("pdfs_processed", result.completed)
            self.toast(result.message)
            return

        self.show_result(result.message, result.outputs,
                         " → ".join(s.label for s in self.steps if s.enabled),
                         result.warnings)
        self.record("Workflow", result.source, result.output, 0,
                    f"{result.steps_run} steps")
        db.bump_stat("pdfs_processed")
        if result.outputs:
            utils.open_path(result.outputs[0])
        self.toast(result.message)
