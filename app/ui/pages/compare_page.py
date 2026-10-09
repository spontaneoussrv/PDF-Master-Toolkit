"""Compare PDFs."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from app import db, paths
from app.core import compare as cmp_mod
from app.core import utils
from app.core.pdfbase import needs_password
from app.ui import components as C
from app.ui.pages.base import ToolPage


class ComparePage(ToolPage):
    TITLE = "Compare PDFs"
    SUBTITLE = "Find what changed between two documents"
    ACTION = "Compare"
    HISTORY_MODULE = "Compare"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self.file_a: Path | None = None
        self.file_b: Path | None = None
        self._result: cmp_mod.CompareResult | None = None

    def build(self) -> None:
        row = C.columns(self._file_card("A"), self._file_card("B"))
        self.scroll.add_layout(row)

        card = C.Card("How to compare")
        self.mode = C.SegmentedControl([
            ("text", "Text differences"),
            ("visual", "Visual differences"),
            ("both", "Both"),
        ], "text")
        self.mode.changed.connect(self._on_mode)
        card.add(C.FormRow("Comparison", self.mode))

        self.mode_hint = C.muted(
            "Compares the words on each page and reports what was added, removed "
            "or changed.")
        card.add(self.mode_hint)

        self.ignore_ws = C.check("Ignore differences in spacing", True)
        self.ignore_case = C.check("Ignore upper and lower case", False)
        self.ignore_numbers = C.check("Ignore numbers", False,
                                      "Useful when only dates or totals differ.")
        card.add(self.ignore_ws)
        card.add(self.ignore_case)
        card.add(self.ignore_numbers)

        self.threshold = C.spin(28, 1, 200)
        self.threshold_row = C.FormRow(
            "Pixel sensitivity", self.threshold,
            "How different a pixel must be to count. Lower finds more, including "
            "rendering noise.")
        self.threshold_row.setVisible(False)
        card.add(self.threshold_row)

        self.diff_images = C.check("Save highlighted difference images", False)
        self.diff_images.setVisible(False)
        card.add(self.diff_images)
        self.scroll.add(card)

        self.add_run_section()

        self.summary_card = C.Card("Summary")
        self.summary_row = QHBoxLayout()
        self.summary_row.setSpacing(24)
        self.summary_card.add_layout(self.summary_row)
        self.summary_card.setVisible(False)
        self.scroll.add(self.summary_card)

        self.results_card = C.Card("Differences")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Page", "Change", "Document A", "Document B"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setMinimumHeight(320)
        self.table.setWordWrap(True)
        self.results_card.add(self.table)

        export_row = QHBoxLayout()
        for text, fmt in [("Export as PDF", "pdf"), ("Export as HTML", "html"),
                          ("Export as CSV", "csv"), ("Export as text", "txt")]:
            b = QPushButton(text)
            b.setObjectName("ChipButton")
            b.clicked.connect(lambda _=False, f=fmt: self._export(f))
            export_row.addWidget(b)
        export_row.addStretch(1)
        self.results_card.add_layout(export_row)
        self.results_card.setVisible(False)
        self.scroll.add(self.results_card)

    def _file_card(self, letter: str) -> C.Card:
        card = C.Card(f"Document {letter}",
                      "The original" if letter == "A" else "The revised version")
        drop = C.DropZone(f"Drop document {letter} here", hint="or click to browse",
                          compact=True)
        drop.files_dropped.connect(
            lambda paths, l=letter: self._set_file(l, paths[0]))
        card.add(drop)

        label = QLabel("")
        label.setObjectName("Subtle")
        label.setWordWrap(True)
        label.setVisible(False)
        card.add(label)

        setattr(self, f"drop_{letter.lower()}", drop)
        setattr(self, f"label_{letter.lower()}", label)
        return card

    def _set_file(self, letter: str, path: str) -> None:
        p = Path(path)
        if not p.is_file():
            return
        password = None
        if needs_password(p):
            from PySide6.QtWidgets import QInputDialog, QLineEdit
            from app.core.pdfbase import open_pdf
            pw, ok = QInputDialog.getText(
                self, "Password required",
                f"'{p.name}' is password protected.\nEnter the password:",
                QLineEdit.Password)
            if not ok:
                return
            try:
                d = open_pdf(p, pw)
                d.close()
                password = pw
            except Exception:
                self.toast("That password was not accepted", "error")
                return

        from app.core.pdf_ops import inspect
        info = inspect(p, password)
        if letter == "A":
            self.file_a, self.password_a = p, password
        else:
            self.file_b, self.password_b = p, password

        label = getattr(self, f"label_{letter.lower()}")
        label.setText(f"📄  {p.name}     ·     {info.pages} pages     ·     "
                      f"{utils.human_size(info.size)}")
        label.setVisible(True)
        getattr(self, f"drop_{letter.lower()}").setVisible(False)

        if self.file_a and self.file_b:
            self.set_status("Ready to compare")

    def _on_mode(self, value: str) -> None:
        self.mode_hint.setText({
            "text": "Compares the words on each page and reports what was added, "
                    "removed or changed.",
            "visual": "Renders both documents and measures how many pixels differ. "
                      "Catches moved images and layout changes that a text diff misses.",
            "both": "Runs both comparisons and reports everything.",
        }[value])
        visual = value in ("visual", "both")
        self.threshold_row.setVisible(visual)
        self.diff_images.setVisible(visual)

    def handle_files(self, paths: Sequence[str]) -> None:
        pdfs = [p for p in paths if str(p).lower().endswith(".pdf")]
        if not pdfs:
            return
        if not self.file_a:
            self._set_file("A", pdfs[0])
            if len(pdfs) > 1:
                self._set_file("B", pdfs[1])
        elif not self.file_b:
            self._set_file("B", pdfs[0])

    def validate(self) -> str:
        if not self.file_a or not self.file_b:
            return "Choose both documents."
        if utils.is_same_file(self.file_a, self.file_b):
            return "Those are the same file."
        return ""

    def start(self) -> None:
        opts = cmp_mod.CompareOptions(
            mode=self.mode.value(),
            ignore_whitespace=self.ignore_ws.isChecked(),
            ignore_case=self.ignore_case.isChecked(),
            ignore_numbers=self.ignore_numbers.isChecked(),
            pixel_threshold=self.threshold.value(),
            write_diff_images=self.diff_images.isChecked(),
            diff_dir=self.file_a.parent / f"{self.file_a.stem}_diff",
        )
        self.run_job(cmp_mod.compare, self.file_a, self.file_b, opts,
                     getattr(self, "password_a", None),
                     getattr(self, "password_b", None),
                     name="Comparing documents")

    def on_success(self, result: cmp_mod.CompareResult) -> None:
        self._result = result
        self._fill_summary(result)
        self._fill_table(result)
        self.results_card.setVisible(True)
        self.show_result(result.message, [],
                         "Identical." if result.identical else
                         "Scroll the table for every difference, then export a report.")
        self.record("Compare", self.file_a, None, result.pages_a, result.message)
        self.toast(result.message)

    def _fill_summary(self, r: cmp_mod.CompareResult) -> None:
        while self.summary_row.count():
            item = self.summary_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        metrics = [
            (f"{r.similarity * 100:.1f}%", "Similar"),
            (str(r.changed_pages), "Pages changed"),
            (str(r.added_pages), "Pages added"),
            (str(r.removed_pages), "Pages removed"),
            (str(r.total_changes), "Total differences"),
        ]
        for value, label in metrics:
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
            v = QLabel(value)
            v.setObjectName("StatValue")
            l = QLabel(label)
            l.setObjectName("StatLabel")
            lay.addWidget(v)
            lay.addWidget(l)
            self.summary_row.addWidget(w)
        self.summary_row.addStretch(1)
        self.summary_card.setVisible(True)

    def _fill_table(self, r: cmp_mod.CompareResult) -> None:
        rows = []
        for page in r.pages:
            if page.status == "identical":
                continue
            if not page.changes:
                rows.append((page.index, page.status.title(), "", ""))
            for c in page.changes[:400]:
                rows.append((page.index, c.label, c.text_a, c.text_b))

        self.table.setRowCount(len(rows))
        colors = {"Added": QColor(233, 248, 240), "Removed": QColor(253, 236, 234),
                  "Changed": QColor(255, 246, 230)}
        for i, (page, kind, a, b) in enumerate(rows):
            for col, value in enumerate([str(page), kind, utils.elide(a, 160),
                                         utils.elide(b, 160)]):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if kind in colors and col >= 1:
                    item.setBackground(colors[kind])
                self.table.setItem(i, col, item)
        self.table.resizeRowsToContents()
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)

    def _export(self, fmt: str) -> None:
        if not self._result:
            self.toast("Run a comparison first", "warning")
            return
        from PySide6.QtWidgets import QFileDialog
        from app.core.jobs import NullContext

        default = str(self.file_a.parent /
                      f"{self.file_a.stem}_vs_{self.file_b.stem}.{fmt}")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save comparison report", default, f"{fmt.upper()} (*.{fmt})")
        if not path:
            return
        try:
            out = cmp_mod.export_report(NullContext(), self._result, path,
                                        self.file_a, self.file_b, fmt)
        except Exception as e:
            self.report_failure(str(e))
            return
        self.toast(f"Report saved as {out.name}")
        utils.open_path(out)
