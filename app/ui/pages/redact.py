"""Redact PDF — permanent content removal."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
                               QPushButton, QTableWidget, QTableWidgetItem)

from app import db
from app.core import redaction, utils
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class RedactPage(SingleFileToolPage):
    TITLE = "Redact PDF"
    SUBTITLE = "Permanently remove sensitive content"
    ACTION = "Find matches"
    OUTPUT_SUFFIX = "redacted"
    HISTORY_MODULE = "Redact"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self.areas: list[redaction.RedactionArea] = []
        self._terms: list[str] = []

    def build(self) -> None:
        self.scroll.add(C.Banner(redaction.REDACTION_WARNING, "danger"))
        self.add_input_section()

        card = C.Card("What to redact")
        self.terms = QPlainTextEdit()
        self.terms.setPlaceholderText(
            "One word or phrase per line, for example:\n"
            "John Smith\n"
            "account number 4471\n"
            "confidential@example.com")
        self.terms.setFixedHeight(110)
        card.add(C.FormRow("Words and phrases", self.terms, label_width=150))

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Common patterns:"))
        for name in redaction.PRESET_PATTERNS:
            b = QPushButton(name)
            b.setObjectName("ChipButton")
            b.clicked.connect(lambda _=False, n=name: self._add_preset(n))
            preset_row.addWidget(b)
        preset_row.addStretch(1)
        card.add_layout(preset_row)

        self.regex = C.check(
            "Treat each line as a regular expression", False,
            "Turn this on to use the pattern buttons above, or to write your own.")
        self.case_sensitive = C.check("Match upper and lower case exactly", False)
        self.whole_word = C.check("Whole words only", False)
        card.add(self.regex)
        card.add(self.case_sensitive)
        card.add(self.whole_word)

        self.pages = C.PageRangeEdit(0, default="all")
        card.add(C.FormRow("Search in pages", self.pages))
        self.scroll.add(card)

        look = C.Card("How the redaction should look")
        self.fill = C.check("Draw a filled box over the removed area", True)
        look.add(self.fill)
        self.fill_color = C.ColorPicker("#000000")
        look.add(C.FormRow("Box colour", self.fill_color))
        self.overlay = QPlainTextEdit()
        self.overlay.setFixedHeight(46)
        self.overlay.setPlaceholderText("Optional label, e.g. REDACTED")
        look.add(C.FormRow("Label inside the box", self.overlay, label_width=150))
        self.scroll.add(look)

        safety = C.Card("Safety")
        self.backup = C.check(
            "Save a copy of the original first", True,
            "Writes document_original.pdf alongside the file before redacting.")
        self.remove_meta = C.check("Also clear the document metadata", True)
        self.remove_imgs = C.check("Erase image pixels under the marks", True,
                                   "Rewrites affected images so the hidden pixels "
                                   "are gone, not merely covered.")
        safety.add(self.backup)
        safety.add(self.remove_meta)
        safety.add(self.remove_imgs)
        self.scroll.add(safety)

        self.add_output_section()
        self.add_run_section("Find matches")

        self.matches_card = C.Card("Matches found",
                                   "Check them before applying — redaction cannot be undone.")
        self.match_table = QTableWidget(0, 3)
        self.match_table.setHorizontalHeaderLabels(["Page", "Term", "Position"])
        self.match_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.match_table.setMinimumHeight(200)
        self.matches_card.add(self.match_table)

        row = QHBoxLayout()
        self.apply_btn = QPushButton("Apply redaction permanently")
        self.apply_btn.setObjectName("DangerButton")
        self.apply_btn.clicked.connect(self._apply)
        row.addWidget(self.apply_btn)
        again = QPushButton("Search again")
        again.setObjectName("Ghost")
        again.clicked.connect(self._on_run_clicked)
        row.addWidget(again)
        row.addStretch(1)
        self.matches_card.add_layout(row)
        self.matches_card.setVisible(False)
        self.scroll.add(self.matches_card)

        self.verify_card = C.Card("Verification",
                                  "A text search of the finished file, so you can see "
                                  "the content really is gone.")
        self.verify_label = QLabel("")
        self.verify_label.setWordWrap(True)
        self.verify_card.add(self.verify_label)
        self.verify_card.setVisible(False)
        self.scroll.add(self.verify_card)

    def _add_preset(self, name: str) -> None:
        pattern = redaction.PRESET_PATTERNS[name]
        current = self.terms.toPlainText().strip()
        self.terms.setPlainText((current + "\n" if current else "") + pattern)
        self.regex.setChecked(True)

    def on_source_changed(self) -> None:
        self.pages.set_total(self.page_count)
        self.matches_card.setVisible(False)
        self.verify_card.setVisible(False)
        self.areas = []

    def _term_list(self) -> list[str]:
        return [t.strip() for t in self.terms.toPlainText().splitlines() if t.strip()]

    def validate(self) -> str:
        base = super().validate()
        if base:
            return base
        if not self._term_list():
            return "Enter at least one word or phrase to redact."
        return ""

    def start(self) -> None:
        self._terms = self._term_list()
        self.run_job(redaction.find_text, self.source, self._terms,
                     self.pages.text(), self.case_sensitive.isChecked(),
                     self.whole_word.isChecked(), self.regex.isChecked(),
                     self.password,
                     name=f"Searching {self.source.name}")

    def on_success(self, result) -> None:
        if isinstance(result, list):
            self._show_matches(result)
        else:
            self._after_redaction(result)

    def _show_matches(self, areas) -> None:
        self.areas = areas
        self.match_table.setRowCount(len(areas))
        for i, a in enumerate(areas):
            x0, y0, x1, y1 = a.rect
            for col, value in enumerate([
                    str(a.page), a.label,
                    f"{x0:.0f}, {y0:.0f}  →  {x1:.0f}, {y1:.0f}"]):
                item = QTableWidgetItem(value)
                self.match_table.setItem(i, col, item)
        self.match_table.resizeColumnsToContents()
        self.match_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

        if not areas:
            self.matches_card.setVisible(False)
            self.show_result("No matches found", [],
                             "None of those terms appear in the searched pages. "
                             "If this is a scanned document, run OCR first — a "
                             "picture of text cannot be searched.")
            self.set_status("No matches found", "warning")
            return

        pages = len({a.page for a in areas})
        self.matches_card.setVisible(True)
        self.show_result(f"Found {len(areas)} match(es) across {pages} page(s)", [],
                         "Review the list, then apply the redaction.")
        self.toast(f"Found {len(areas)} match(es)")

    def _apply(self) -> None:
        if not self.areas:
            self.toast("Search for matches first", "warning")
            return
        if not self.confirm(
            "Apply redaction permanently?",
            f"{len(self.areas)} area(s) will be permanently removed from "
            f"{self.source.name}.",
            "The text and image pixels under each mark are deleted from the file. "
            "This cannot be undone." +
            ("\n\nA copy of the original will be saved first."
             if self.backup.isChecked() else
             "\n\nNo backup will be made — you have turned that option off."),
            destructive=True,
        ):
            return

        opts = redaction.RedactOptions(
            fill_color=tuple(c / 255 for c in _rgb(self.fill_color.color())),
            draw_box=self.fill.isChecked(),
            overlay_text=self.overlay.toPlainText().strip(),
            remove_images=self.remove_imgs.isChecked(),
            remove_metadata=self.remove_meta.isChecked(),
            make_backup=self.backup.isChecked(),
            case_sensitive=self.case_sensitive.isChecked(),
            whole_word=self.whole_word.isChecked(),
        )
        target = self.output.resolved(self.source, "redacted")
        self.run_job(redaction.redact, self.source, self.areas, target, opts,
                     self.password, name=f"Redacting {self.source.name}")

    def _after_redaction(self, result) -> None:
        extra = []
        if result.backup:
            extra.append(f"Original copied to {result.backup.name}")
        self.show_result(result.message,
                         [result.output] if result.output else [],
                         "\n".join(extra), result.warnings)

        if result.output:
            counts = redaction.verify_removal(result.output, self._terms)
            remaining = {k: v for k, v in counts.items() if v}
            if remaining:
                self.verify_label.setText(
                    "⚠  Some search terms still appear in the finished file:\n" +
                    "\n".join(f"   • “{k}” — {v} occurrence(s)"
                              for k, v in remaining.items()) +
                    "\n\nThis usually means the text also occurs in a form field, "
                    "an annotation or an attachment, which are not covered by area "
                    "redaction. Check the file before sharing it.")
            else:
                self.verify_label.setText(
                    "✓  A full text search of the finished file found none of the "
                    "redacted terms. The content was removed, not merely covered.")
            self.verify_card.setVisible(True)

        self.record("Redact", self.source, result.output, result.pages_affected,
                    f"{result.areas_applied} areas")
        db.bump_stat("pdfs_processed")
        self.matches_card.setVisible(False)
        self.areas = []
        self.finish_output([result.output] if result.output else [], self.output)
        self.toast("Redaction applied")
        if self.run_bar:
            self.run_bar.set_action_text("Find matches")


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
