"""Extract Text."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton

from app import db
from app.core import textops, utils
from app.core.jobs import NullContext
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class ExtractTextPage(SingleFileToolPage):
    TITLE = "Extract Text"
    SUBTITLE = "Pull the text out of a PDF"
    ACTION = "Extract text"
    OUTPUT_SUFFIX = "text"
    OUTPUT_EXT = ".txt"
    HISTORY_MODULE = "Extract Text"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self._result: textops.TextResult | None = None

    def build(self) -> None:
        self.add_input_section()

        self.scanned_banner = C.Banner("", "warning")
        self.scanned_banner.setVisible(False)
        self.scroll.add(self.scanned_banner)

        self.ocr_row = QHBoxLayout()
        self.ocr_btn = QPushButton("Run OCR on this document")
        self.ocr_btn.setObjectName("Primary")
        self.ocr_btn.clicked.connect(self._send_to_ocr)
        self.ocr_btn.setVisible(False)
        self.ocr_row.addWidget(self.ocr_btn)
        self.ocr_row.addStretch(1)
        self.scroll.add_layout(self.ocr_row)

        card = C.Card("Options")
        self.pages = C.PageRangeEdit(0, default="all")
        card.add(C.FormRow("Pages", self.pages))

        self.layout_mode = C.combo([
            ("text", "Reading order (recommended)"),
            ("blocks", "Block by block, with blank lines between"),
            ("words", "Rebuilt line by line from word positions"),
            ("html", "HTML with basic formatting"),
        ], "text")
        card.add(C.FormRow("Layout", self.layout_mode))

        self.page_breaks = C.check("Mark where each page begins", True)
        self.dehyphenate = C.check("Join words split across lines", True)
        self.collapse = C.check("Collapse all whitespace into single spaces", False)
        card.add(self.page_breaks)
        card.add(self.dehyphenate)
        card.add(self.collapse)
        self.scroll.add(card)

        self.add_run_section()

        self.text_card = C.Card("Extracted text", "Edit it here before exporting.")
        self.stats_label = QLabel("")
        self.stats_label.setObjectName("Subtle")
        self.text_card.add(self.stats_label)

        self.text_view = QPlainTextEdit()
        self.text_view.setMinimumHeight(300)
        self.text_card.add(self.text_view)

        row = QHBoxLayout()
        for text, slot in [("Copy", self._copy),
                           ("Save as TXT", lambda: self._export("txt")),
                           ("Save as Word", lambda: self._export("docx")),
                           ("Save as JSON", lambda: self._export("json")),
                           ("Save as CSV", lambda: self._export("csv")),
                           ("Save as Markdown", lambda: self._export("md"))]:
            b = QPushButton(text)
            b.setObjectName("ChipButton")
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        self.text_card.add_layout(row)
        self.text_card.setVisible(False)
        self.scroll.add(self.text_card)

    def on_source_changed(self) -> None:
        self.pages.set_total(self.page_count)
        self.scanned_banner.setVisible(False)
        self.ocr_btn.setVisible(False)

    def start(self) -> None:
        opts = textops.TextExtractOptions(
            pages=self.pages.text(),
            layout=self.layout_mode.currentData(),
            keep_page_breaks=self.page_breaks.isChecked(),
            dehyphenate_lines=self.dehyphenate.isChecked(),
            collapse_whitespace=self.collapse.isChecked(),
        )
        self.run_job(textops.extract_text, self.source, opts, self.password,
                     name=f"Extracting text from {self.source.name}")

    def on_success(self, result: textops.TextResult) -> None:
        self._result = result
        self.text_view.setPlainText(result.text)
        self.text_card.setVisible(True)
        self.stats_label.setText(
            f"{result.words:,} words   ·   {result.characters:,} characters   ·   "
            f"{len(result.pages)} page(s)" +
            (f"   ·   {len(result.empty_pages)} page(s) had no text"
             if result.empty_pages else ""))

        if result.looks_scanned and result.characters < 200:
            self.scanned_banner.set_kind("warning")
            self.scanned_banner.set_text(
                "This document appears to be scanned — it contains little or no "
                "embedded text. Run OCR to recognise the words in the page images.")
            self.scanned_banner.setVisible(True)
            self.ocr_btn.setVisible(True)
            self.show_result("No usable text found", [], textops.SCANNED_HINT)
            self.set_status(textops.SCANNED_HINT, "warning")
        else:
            self.show_result(result.message, [],
                             "Use the buttons under the text to save or copy it.")
            self.record("Extract text", self.source, None, len(result.pages),
                        f"{result.words} words")
            db.bump_stat("pdfs_processed")

    def _send_to_ocr(self) -> None:
        if not self.source:
            return
        path = str(self.source)
        self.go("ocr")
        if self.window_ref is not None:
            page = self.window_ref.current_page()
            if page is not None and hasattr(page, "handle_files"):
                page.handle_files([path])

    def _copy(self) -> None:
        text = self.text_view.toPlainText()
        if not text.strip():
            self.toast("There is no text to copy", "warning")
            return
        C.copy_to_clipboard(text)
        self.toast(f"Copied {len(text.split()):,} words")

    def _export(self, fmt: str) -> None:
        from PySide6.QtWidgets import QFileDialog

        text = self.text_view.toPlainText()
        if not text.strip():
            self.toast("There is no text to save", "warning")
            return
        default = str(self.source.parent / f"{self.source.stem}_text.{fmt}")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save extracted text", default, f"{fmt.upper()} (*.{fmt})")
        if not path:
            return

        result = self._result or textops.TextResult()
        result.text = text
        if not result.pages:
            result.pages = [text]
        try:
            out = textops.export_text(NullContext(), result, path, fmt, self.source.name)
        except Exception as e:
            self.report_failure(str(e))
            return
        self.record("Export text", self.source, out, len(result.pages), fmt)
        db.bump_stat("files_converted")
        self.toast(f"Saved {out.name}")
        utils.reveal_in_explorer(out)
