"""
Visit Sorter.

The workflow from the supplied ``Get Set.py`` and ``Arrange Only.py`` scripts,
rebuilt as a proper screen:

    1. Detect      scan the PDF for visit sets (RN / Aide, by date)
    2. Review      correct the dates and page numbers in a table, or in Excel
    3. Rebuild     write the PDF back in date order
    4. Split       optionally break it into parts under a size limit

Detection rules are editable, so a new form layout is a settings change rather
than a code change.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from app import db
from app.core import utils, visits
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class VisitsPage(SingleFileToolPage):
    TITLE = "Visit Sorter"
    SUBTITLE = "Detect visit sets, sort by date and split by size"
    ACTION = "Detect visits"
    OUTPUT_SUFFIX = "sorted"
    HISTORY_MODULE = "Visit Sorter"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self.visits: list[visits.Visit] = []
        self._worksheet: Path | None = None

    def build(self) -> None:
        self.scroll.add(C.Banner(
            "Built for care-documentation PDFs that hold many visit sets in "
            "arbitrary order. It reads the visit date from each page, groups the "
            "pages into sets, and rebuilds the document in date order.", "info"))

        self.add_input_section()

        rules_card = C.Card("Detection rules",
                            "Each rule finds a visit header. Edit the patterns if "
                            "your forms use different wording.")
        self.rule_widgets = []
        for rule in visits.DEFAULT_RULES:
            holder = QWidget()
            lay = QVBoxLayout(holder)
            lay.setContentsMargins(0, 4, 0, 8)
            lay.setSpacing(6)

            head = QHBoxLayout()
            enabled = C.check(rule.name, rule.enabled)
            head.addWidget(enabled)
            head.addStretch(1)
            lay.addLayout(head)

            date_pat = QLineEdit(rule.date_pattern)
            date_pat.setObjectName("Mono")
            lay.addWidget(C.FormRow("Date pattern", date_pat, label_width=120))

            end_pat = QLineEdit(rule.end_pattern)
            end_pat.setObjectName("Mono")
            lay.addWidget(C.FormRow("End-of-set marker", end_pat, label_width=120))

            rules_card.add(holder)
            self.rule_widgets.append((rule, enabled, date_pat, end_pat))

        self.group_same = C.check(
            "Combine sets that share a date and type", True,
            "Two Aide visits on the same day become one entry, with a set count.")
        rules_card.add(self.group_same)
        self.fallback = C.check(
            "Fall back to a generic date search if no rule matches", True)
        rules_card.add(self.fallback)
        self.scroll.add(rules_card)

        self.add_run_section("Detect visits")

        # ---- review table -------------------------------------------------
        self.review_card = C.Card(
            "Detected visits",
            "Edit the Date and Pages columns directly, or export to Excel, correct "
            "it there, and load it back.")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Type", "Date (mm/dd/yyyy)", "Pages", "Sets", "Page count"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setMinimumHeight(280)
        self.table.itemChanged.connect(self._on_cell_edited)
        self.review_card.add(self.table)

        row = QHBoxLayout()
        for text, slot in [("Add a row", self._add_row),
                           ("Remove selected", self._remove_rows),
                           ("Sort by date", self._sort_rows),
                           ("Export to Excel", self._export_excel),
                           ("Load from Excel", self._import_excel)]:
            b = QPushButton(text)
            b.setObjectName("ChipButton")
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        self.review_card.add_layout(row)

        self.coverage = QLabel("")
        self.coverage.setObjectName("Subtle")
        self.coverage.setWordWrap(True)
        self.review_card.add(self.coverage)
        self.review_card.setVisible(False)
        self.scroll.add(self.review_card)

        # ---- output --------------------------------------------------------
        self.build_card = C.Card("Rebuild")
        self.bookmarks = C.check("Add a bookmark for each visit", True)
        self.build_card.add(self.bookmarks)

        self.split_enabled = C.check(
            "Also split the result into size-limited parts", False,
            "Visits are kept whole wherever possible — a part is closed before a "
            "visit that would not fit.")
        self.split_enabled.toggled.connect(
            lambda on: self.max_mb_row.setVisible(on))
        self.build_card.add(self.split_enabled)

        self.max_mb = C.dspin(10.0, 0.1, 2048.0, " MB", 0.5, 2)
        self.max_mb_row = C.FormRow("Maximum size per part", self.max_mb)
        self.max_mb_row.setVisible(False)
        self.build_card.add(self.max_mb_row)

        self.build_btn = QPushButton("Rebuild in date order")
        self.build_btn.setObjectName("PrimaryLarge")
        self.build_btn.clicked.connect(self._build)
        self.build_card.add(self.build_btn)
        self.build_card.setVisible(False)
        self.scroll.add(self.build_card)

        self.add_output_section()
        self.output.setVisible(False)

    # -- detection ---------------------------------------------------------
    def _rules(self) -> list[visits.VisitRule]:
        out = []
        for rule, enabled, date_pat, end_pat in self.rule_widgets:
            out.append(visits.VisitRule(
                name=rule.name,
                date_pattern=date_pat.text().strip() or rule.date_pattern,
                end_pattern=end_pat.text().strip(),
                new_date_ends_set=rule.new_date_ends_set,
                enabled=enabled.isChecked(),
            ))
        return out

    def on_source_changed(self) -> None:
        self.review_card.setVisible(False)
        self.build_card.setVisible(False)
        self.output.setVisible(False)
        self.visits = []

    def start(self) -> None:
        self.run_job(visits.detect_visits, self.source, self._rules(), self.password,
                     group_same_date=self.group_same.isChecked(),
                     use_fallback=self.fallback.isChecked(),
                     name=f"Scanning {self.source.name}")

    def on_success(self, result) -> None:
        if isinstance(result, visits.DetectionResult):
            self._show_detection(result)
        else:
            self._after_build(result)

    def _show_detection(self, result: visits.DetectionResult) -> None:
        self.visits = result.visits
        self._fill_table()
        self.review_card.setVisible(True)
        self.build_card.setVisible(True)
        self.output.setVisible(True)
        self.show_result(result.message, [], "", result.warnings)
        self.set_status(result.message, "success" if result.visits else "warning")
        if not result.visits:
            self.toast("No visits were detected — check the rules", "warning")
        else:
            self.toast(f"Detected {len(result.visits)} visit(s)")

    # -- table -------------------------------------------------------------
    def _fill_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.visits))
        for i, v in enumerate(self.visits):
            self.table.setItem(i, 0, QTableWidgetItem(v.kind))
            self.table.setItem(i, 1, QTableWidgetItem(v.date_text))
            self.table.setItem(i, 2, QTableWidgetItem(v.pages_text))
            sets_item = QTableWidgetItem(str(v.sets))
            sets_item.setFlags(sets_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 3, sets_item)
            count_item = QTableWidgetItem(str(v.page_count))
            count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 4, count_item)
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._update_coverage()

    def _read_table(self) -> list[visits.Visit]:
        out: list[visits.Visit] = []
        total = self.page_count
        for i in range(self.table.rowCount()):
            kind = (self.table.item(i, 0).text() if self.table.item(i, 0) else "").strip()
            date_text = (self.table.item(i, 1).text() if self.table.item(i, 1) else "").strip()
            pages_text = (self.table.item(i, 2).text() if self.table.item(i, 2) else "").strip()
            if not pages_text:
                continue
            pages = utils.parse_page_ranges(pages_text, total)
            if not pages:
                continue
            dt = visits._parse_any_date(date_text)
            sets = int(self.table.item(i, 3).text()) if self.table.item(i, 3) else 1
            out.append(visits.Visit(dt, kind or "VISIT", pages, sets))
        return out

    def _on_cell_edited(self, item) -> None:
        row = item.row()
        if item.column() in (1, 2):
            self.table.blockSignals(True)
            pages = utils.parse_page_ranges(
                self.table.item(row, 2).text() if self.table.item(row, 2) else "",
                self.page_count)
            if self.table.item(row, 4):
                self.table.item(row, 4).setText(str(len(pages)))
            # flag an unparseable date
            if item.column() == 1:
                bad = bool(item.text().strip()) and \
                    visits._parse_any_date(item.text()) is None
                item.setBackground(Qt.red if bad else Qt.transparent)
                item.setToolTip("Use mm/dd/yyyy, e.g. 03/12/2026" if bad else "")
            self.table.blockSignals(False)
        self._update_coverage()

    def _update_coverage(self) -> None:
        current = self._read_table()
        used = sorted({p for v in current for p in v.pages})
        total = self.page_count
        missing = [p for p in range(1, total + 1) if p not in set(used)]
        duplicates = len([p for v in current for p in v.pages]) - len(used)

        bits = [f"{len(current)} visit(s)", f"{len(used)} of {total} pages included"]
        if missing:
            bits.append(f"not included: {utils.format_page_ranges(missing)[:90]}")
        if duplicates:
            bits.append(f"{duplicates} page(s) appear in more than one visit")
        undated = sum(1 for v in current if v.date is None)
        if undated:
            bits.append(f"{undated} visit(s) have no valid date and will sort last")
        self.coverage.setText("     ·     ".join(bits))

    def _add_row(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem("VISIT"))
        self.table.setItem(row, 1, QTableWidgetItem(datetime.now().strftime("%m/%d/%Y")))
        self.table.setItem(row, 2, QTableWidgetItem(""))
        self.table.setItem(row, 3, QTableWidgetItem("1"))
        self.table.setItem(row, 4, QTableWidgetItem("0"))

    def _remove_rows(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedItems()}, reverse=True):
            self.table.removeRow(row)
        self._update_coverage()

    def _sort_rows(self) -> None:
        self.visits = sorted(self._read_table(),
                             key=lambda v: v.date or datetime.max)
        self._fill_table()

    # -- Excel round-trip --------------------------------------------------
    def _export_excel(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        current = self._read_table()
        if not current:
            self.toast("There is nothing to export", "warning")
            return
        default = str(self.source.with_name(f"{self.source.stem}_visit_edit.xlsx"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Export the visit worksheet", default, "Excel (*.xlsx)")
        if not path:
            return
        try:
            out = visits.export_worksheet(current, path)
        except Exception as e:
            self.report_failure(str(e))
            return
        self._worksheet = out
        self.toast(f"Worksheet saved as {out.name}")
        utils.open_path(out)

    def _import_excel(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        start = str(self._worksheet) if self._worksheet else str(
            self.source.parent if self.source else Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Load the corrected worksheet", start, "Excel (*.xlsx)")
        if not path:
            return
        try:
            loaded = visits.load_worksheet(path, self.page_count)
        except Exception as e:
            self.report_failure(str(e))
            return
        self.visits = loaded
        self._fill_table()
        self.toast(f"Loaded {len(loaded)} visit(s) from the worksheet")

    # -- rebuild -----------------------------------------------------------
    def _build(self) -> None:
        current = self._read_table()
        if not current:
            self.toast("There are no visits to rebuild", "warning")
            return
        self.visits = current

        if self.split_enabled.isChecked():
            folder = self.output.dir_picker.path() or \
                self.source.parent / f"{self.source.stem}_parts"
            self.run_job(visits.split_by_size, self.source, current,
                         self.max_mb.value(), folder, self.password,
                         name=f"Splitting {self.source.name}")
        else:
            target = self.output.resolved(self.source, "sorted")
            self.run_job(visits.build_sorted_pdf, self.source, current, target,
                         self.password, add_bookmarks=self.bookmarks.isChecked(),
                         name=f"Rebuilding {self.source.name}")

    def _after_build(self, result: visits.BuildResult) -> None:
        detail = f"{result.visits} visit(s) in date order"
        if result.parts:
            detail += f" · {result.parts} part(s)"
        self.show_result(result.message, result.outputs, detail, result.warnings)
        self.record("Visit Sorter", self.source,
                    result.outputs[0] if result.outputs else None, result.pages,
                    f"{result.visits} visits")
        db.bump_stat("pdfs_processed")
        db.bump_stat("pages_processed", result.pages)
        self.finish_output(result.outputs, self.output)
        self.toast(result.message)
