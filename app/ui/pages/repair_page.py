"""Repair PDF."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app import db
from app.core import deps, repair, utils
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class RepairPage(SingleFileToolPage):
    TITLE = "Repair PDF"
    SUBTITLE = "Recover a damaged or unreadable file"
    ACTION = "Check this file"
    OUTPUT_SUFFIX = "repaired"
    HISTORY_MODULE = "Repair"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self._diagnosis = None

    def build(self) -> None:
        self.scroll.add(C.Banner(
            "Not every damaged PDF can be repaired. Where the original data is "
            "missing rather than merely misplaced, no tool can bring it back. This "
            "screen always reports how many pages were recovered and how many "
            "were lost.", "info"))

        self.add_input_section("The original file is never modified — a repaired "
                               "copy is written alongside it.")

        self.diag_card = C.Card("Diagnosis")
        self.diag_summary = QLabel("")
        self.diag_summary.setWordWrap(True)
        self.diag_card.add(self.diag_summary)
        self.issues_box = QVBoxLayout()
        self.issues_box.setSpacing(6)
        self.diag_card.add_layout(self.issues_box)
        self.diag_card.setVisible(False)
        self.scroll.add(self.diag_card)

        opts = C.Card("Recovery options")
        self.deep = C.check(
            "Try deep reconstruction if the usual methods fail", True,
            "Rebuilds from the raw bytes. Slower, and may lose page ordering, but "
            "sometimes recovers files nothing else can open.")
        opts.add(self.deep)
        if not deps.has_qpdf():
            opts.add(C.muted(
                "The qpdf structural-repair component is not present in this build, "
                "so one of the four recovery strategies is unavailable. The others "
                "still run."))
        self.scroll.add(opts)

        self.add_output_section()
        self.add_run_section("Check this file")

        self.repair_btn_card = C.Card("Repair")
        self.repair_btn = QPushButton("Attempt repair")
        self.repair_btn.setObjectName("PrimaryLarge")
        self.repair_btn.clicked.connect(self._repair)
        self.repair_btn_card.add(self.repair_btn)
        self.repair_btn_card.setVisible(False)
        self.scroll.add(self.repair_btn_card)

    def set_source(self, path) -> None:
        # a damaged file may not open at all, so bypass the base class's probe
        from pathlib import Path
        p = Path(path)
        if not p.is_file():
            return
        self.source = p
        self.password = None
        self.info = None
        if self.drop is not None:
            self.drop.setVisible(False)
        self.file_summary.setText(
            f"📄  {p.name}     ·     {utils.human_size(utils.file_size(p))}")
        self.file_summary.setVisible(True)
        self.change_btn.setVisible(True)
        self.preview_btn.setVisible(False)
        if self.output is not None:
            self.output.set_source(p, "repaired")
        self.diag_card.setVisible(False)
        self.repair_btn_card.setVisible(False)
        self.set_status("Press “Check this file” to diagnose it")

    def validate(self) -> str:
        if not self.source:
            return "Choose a PDF first."
        return ""

    def start(self) -> None:
        self.run_job(repair.diagnose, self.source, self.password,
                     name=f"Checking {self.source.name}")

    def on_success(self, result) -> None:
        if isinstance(result, repair.Diagnosis):
            self._show_diagnosis(result)
        else:
            self._show_repair(result)

    def _show_diagnosis(self, d: repair.Diagnosis) -> None:
        self._diagnosis = d
        self.diag_card.setVisible(True)
        self.diag_summary.setText(d.summary)

        while self.issues_box.count():
            item = self.issues_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for issue in d.issues:
            kind = {"error": "danger", "warning": "warning", "info": "success"}.get(
                issue.severity, "info")
            self.issues_box.addWidget(C.Banner(issue.message, kind))

        rows = [
            ("File size", utils.human_size(d.file_size)),
            ("Pages found", str(d.page_count) if d.page_count else "—"),
            ("Pages readable", str(d.readable_pages) if d.readable else "—"),
            ("PDF version", d.pdf_version or "—"),
        ]
        info = QLabel("     ·     ".join(f"{k}: {v}" for k, v in rows))
        info.setObjectName("Subtle")
        self.issues_box.addWidget(info)

        needs = d.needs_repair or not d.readable
        self.repair_btn_card.setVisible(True)
        self.repair_btn.setText("Attempt repair" if needs else "Save a clean copy anyway")
        self.show_result(d.summary, [], "")
        self.set_status(d.summary, "warning" if needs else "success")

    def _repair(self) -> None:
        if not self.source:
            return
        target = self.output.resolved(self.source, "repaired")
        self.run_job(repair.repair, self.source, target, self.password,
                     deep=self.deep.isChecked(),
                     name=f"Repairing {self.source.name}")

    def _show_repair(self, result: repair.RepairResult) -> None:
        detail = [f"Method that worked: {result.method}"]
        if result.original_pages:
            detail.append(f"Pages recovered: {result.pages_recovered} of "
                          f"{result.original_pages}")
        if result.pages_failed:
            detail.append(f"Pages lost: {result.pages_failed}")

        self.show_result(result.message, [result.output] if result.output else [],
                         "\n".join(detail), result.warnings)
        self.record("Repair", self.source, result.output, result.pages_recovered,
                    result.method)
        db.bump_stat("pdfs_processed")
        self.finish_output([result.output] if result.output else [], self.output)
        self.toast(f"Recovered {result.pages_recovered} page(s)")
