"""History — what you have processed, with no document content stored."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMenu, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from app import branding, config, db
from app.core import utils
from app.ui import components as C
from app.ui.pages import MODULES
from app.ui.pages.base import BasePage


class HistoryPage(BasePage):
    TITLE = "History"
    SUBTITLE = "Everything you have processed"

    def build(self) -> None:
        self.scroll.add(C.Banner(
            "History records file names, operations and timestamps only. The "
            "contents of your documents are never stored, and passwords are never "
            "written to disk.", "info"))

        stats_card = C.Card("Totals")
        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(28)
        stats_card.add_layout(self.stats_row)
        reset = QPushButton("Reset totals")
        reset.setObjectName("Ghost")
        reset.clicked.connect(self._reset_stats)
        stats_card.add_header_widget(reset)
        self.scroll.add(stats_card)

        filter_card = C.Card("Activity")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by file or operation…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refresh_table)
        row.addWidget(self.search, 1)

        self.module_filter = QComboBox()
        self.module_filter.addItem("All tools", None)
        for m in MODULES:
            self.module_filter.addItem(m.title, m.title)
        self.module_filter.currentIndexChanged.connect(self._refresh_table)
        row.addWidget(self.module_filter)

        self.limit = QComboBox()
        for n in (100, 250, 500, 1000, 5000):
            self.limit.addItem(f"Last {n}", n)
        self.limit.currentIndexChanged.connect(self._refresh_table)
        row.addWidget(self.limit)
        filter_card.add_layout(row)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["When", "Tool", "Operation", "File", "Output", "Size", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setMinimumHeight(380)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.itemDoubleClicked.connect(self._open_output)
        filter_card.add(self.table)

        actions = QHBoxLayout()
        for text, slot in [("Open the selected output", self._open_selected),
                           ("Show in folder", self._reveal_selected),
                           ("Export as CSV", self._export)]:
            b = QPushButton(text)
            b.setObjectName("ChipButton")
            b.clicked.connect(slot)
            actions.addWidget(b)
        actions.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Subtle")
        actions.addWidget(self.count_label)
        filter_card.add_layout(actions)
        self.scroll.add(filter_card)

        privacy = C.Card("Privacy controls")
        self.enabled = C.check("Keep a history of what I process",
                               config.get_bool("history_enabled", True))
        self.enabled.toggled.connect(self._toggle_history)
        privacy.add(self.enabled)

        self.retention = C.combo([
            (0, "Keep forever"),
            (7, "Delete entries older than 7 days"),
            (30, "Delete entries older than 30 days"),
            (90, "Delete entries older than 90 days"),
            (180, "Delete entries older than 180 days"),
            (365, "Delete entries older than a year"),
        ], config.get_int("history_retention_days", 180))
        self.retention.currentIndexChanged.connect(self._set_retention)
        privacy.add(C.FormRow("Automatic clean-up", self.retention))

        row2 = QHBoxLayout()
        clear_recent = QPushButton("Clear recent files")
        clear_recent.setObjectName("Ghost")
        clear_recent.clicked.connect(self._clear_recent)
        clear_all = QPushButton("Delete all history")
        clear_all.setObjectName("DangerGhost")
        clear_all.clicked.connect(self._clear_all)
        row2.addWidget(clear_recent)
        row2.addWidget(clear_all)
        row2.addStretch(1)
        privacy.add_layout(row2)
        self.scroll.add(privacy)

    # -- data --------------------------------------------------------------
    def on_show(self) -> None:
        self._refresh_stats()
        self._refresh_table()

    def _refresh_stats(self) -> None:
        while self.stats_row.count():
            item = self.stats_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        stats = db.get_stats()
        metrics = [
            (f"{int(stats.get('pdfs_processed', 0)):,}", "PDFs processed"),
            (f"{int(stats.get('ocr_documents', 0)):,}", "OCR documents"),
            (f"{int(stats.get('ocr_pages', 0)):,}", "Pages recognised"),
            (f"{int(stats.get('files_converted', 0)):,}", "Files converted"),
            (utils.human_size(stats.get("bytes_saved", 0)), "Space saved"),
            (f"{int(stats.get('batch_jobs', 0)):,}", "Batch runs"),
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
            self.stats_row.addWidget(w)
        self.stats_row.addStretch(1)

    def _refresh_table(self) -> None:
        rows = db.get_history(
            limit=int(self.limit.currentData() or 250),
            module=self.module_filter.currentData(),
            search=self.search.text().strip(),
        )
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            when = r["ts"][:16].replace("T", "   ")
            size = ""
            if r["output_size"]:
                size = utils.human_size(r["output_size"])
                if r["input_size"] and r["output_size"] < r["input_size"]:
                    pct = (1 - r["output_size"] / r["input_size"]) * 100
                    size += f"  (−{pct:.0f}%)"
            elif r["input_size"]:
                size = utils.human_size(r["input_size"])

            values = [when, r["module"], r["operation"],
                      r["input_name"] or "—", r["output_name"] or "—",
                      size, r["status"].title()]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 3 and r["input_path"]:
                    item.setToolTip(r["input_path"])
                if col == 4 and r["output_path"]:
                    item.setToolTip(r["output_path"])
                if col == 6 and r["status"] != "success":
                    item.setToolTip(r["detail"] or "")
                self.table.setItem(i, col, item)
            self.table.item(i, 0).setData(Qt.UserRole, r["output_path"])
            self.table.item(i, 0).setData(Qt.UserRole + 1, r["input_path"])

        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)
        self.table.resizeColumnToContents(6)
        self.count_label.setText(f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'}")

        if not db.history_enabled():
            self.count_label.setText("History is switched off")

    # -- actions -----------------------------------------------------------
    def _selected_output(self) -> str | None:
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows:
            return None
        item = self.table.item(min(rows), 0)
        return item.data(Qt.UserRole) if item else None

    def _open_selected(self) -> None:
        path = self._selected_output()
        if path and Path(path).exists():
            utils.open_path(path)
        else:
            self.toast("That file no longer exists", "warning")

    def _reveal_selected(self) -> None:
        path = self._selected_output()
        if path and Path(path).exists():
            utils.reveal_in_explorer(path)
        else:
            self.toast("That file no longer exists", "warning")

    def _open_output(self, item) -> None:
        self._open_selected()

    def _context_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Open the output file", self._open_selected)
        menu.addAction("Show in folder", self._reveal_selected)
        menu.addSeparator()
        menu.addAction("Copy the output path", self._copy_path)
        menu.exec(self.table.mapToGlobal(pos))

    def _copy_path(self) -> None:
        path = self._selected_output()
        if path:
            C.copy_to_clipboard(path)
            self.toast("Path copied")

    def _export(self) -> None:
        import csv
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self, "Export history", str(Path.home() / "pdf_history.csv"),
            "CSV (*.csv)")
        if not path:
            return
        rows = db.get_history(limit=100000)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["When", "Tool", "Operation", "Input", "Output",
                        "Input size", "Output size", "Pages", "Duration (ms)",
                        "Status", "Detail"])
            for r in rows:
                w.writerow([r["ts"], r["module"], r["operation"], r["input_path"],
                            r["output_path"], r["input_size"], r["output_size"],
                            r["pages"], r["duration_ms"], r["status"], r["detail"]])
        self.toast(f"Exported {len(rows)} entries")
        utils.reveal_in_explorer(path)

    def _toggle_history(self, on: bool) -> None:
        config.set("history_enabled", on)
        self.toast("History enabled" if on else
                   "History switched off — nothing new will be recorded")
        self._refresh_table()

    def _set_retention(self) -> None:
        days = int(self.retention.currentData() or 0)
        config.set("history_retention_days", days)
        if days:
            removed = db.prune_history(days)
            if removed:
                self.toast(f"Removed {removed} old entr{'y' if removed == 1 else 'ies'}")
                self._refresh_table()

    def _clear_recent(self) -> None:
        db.clear_recent()
        self.toast("Recent files cleared")

    def _clear_all(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Delete all history?")
        box.setText("Every entry will be permanently removed.")
        box.setInformativeText("Your processed files themselves are not affected — "
                               "only this record of them.")
        yes = box.addButton("Delete history", QMessageBox.AcceptRole)
        yes.setObjectName("DangerButton")
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        db.clear_history()
        self._refresh_table()
        self.toast("History deleted")

    def _reset_stats(self) -> None:
        if not self.window_ref:
            pass
        db.reset_stats()
        self._refresh_stats()
        self.toast("Totals reset")
