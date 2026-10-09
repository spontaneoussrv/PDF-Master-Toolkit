"""Extract Pages."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QPushButton

from app import db
from app.core import utils
from app.core.pdf_ops import extract_pages, remove_pages
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class ExtractPagesPage(SingleFileToolPage):
    TITLE = "Extract Pages"
    SUBTITLE = "Pull selected pages into a new PDF"
    ACTION = "Extract pages"
    OUTPUT_SUFFIX = "extracted"
    HISTORY_MODULE = "Extract Pages"

    def build(self) -> None:
        self.add_input_section()

        card = C.Card("Pages")
        self.pages = C.PageRangeEdit(0, default="1-5")
        self.pages.textChanged.connect(self._update_preview)
        card.add(C.FormRow(
            "Pages to extract", self.pages,
            "Examples:  1,3,5   ·   10-20   ·   odd   ·   even   ·   1-3,8,12-end"))

        chips = QHBoxLayout()
        for text, value in [("All", "all"), ("Odd", "odd"), ("Even", "even"),
                            ("First 10", "1-10"), ("Last page", "last")]:
            b = QPushButton(text)
            b.setObjectName("ChipButton")
            b.clicked.connect(lambda _=False, v=value: self.pages.setText(v))
            chips.addWidget(b)
        chips.addStretch(1)
        card.add_layout(chips)

        self.preview = C.muted("")
        card.add(self.preview)

        self.mode = C.SegmentedControl([
            ("keep", "Keep the selected pages"),
            ("remove", "Remove the selected pages"),
        ], "keep")
        self.mode.changed.connect(lambda _: self._update_preview())
        card.add(C.FormRow("Action", self.mode))

        self.separate = C.check(
            "Save each page as its own file", False,
            "Produces one PDF per selected page in a folder.")
        self.separate.toggled.connect(self._on_separate)
        card.add(self.separate)
        self.scroll.add(card)

        self.add_output_section()
        self.add_run_section()

    def _on_separate(self, on: bool) -> None:
        self.output.folder_mode = on
        self.output.set_title("Output folder" if on else "Output")
        if on:
            self.mode.set_value("keep")

    def on_source_changed(self) -> None:
        self.pages.set_total(self.page_count)
        self._update_preview()

    def _update_preview(self) -> None:
        if not self.page_count:
            self.preview.setText("")
            return
        selected = self.pages.pages()
        if not selected:
            self.preview.setText("This selection matches no pages.")
            return
        if self.mode.value() == "remove":
            kept = self.page_count - len(selected)
            self.preview.setText(
                f"Removing {len(selected)} page(s) — the result will have {kept} page(s): "
                f"{utils.format_page_ranges([p for p in range(1, self.page_count + 1) if p not in set(selected)])[:120]}")
        else:
            self.preview.setText(
                f"Extracting {len(selected)} of {self.page_count} page(s): "
                f"{utils.format_page_ranges(selected)[:120]}")

    def validate(self) -> str:
        base = super().validate()
        if base:
            return base
        selected = self.pages.pages()
        if not selected:
            return "That page selection matches no pages in this document."
        if self.mode.value() == "remove" and len(selected) >= self.page_count:
            return "That would remove every page."
        return ""

    def start(self) -> None:
        if self.separate.isChecked():
            target = self.output.output_dir()
        else:
            suffix = "extracted" if self.mode.value() == "keep" else "trimmed"
            target = self.output.resolved(self.source, suffix)
        self._sep = self.separate.isChecked()

        if self.mode.value() == "remove":
            self.run_job(remove_pages, self.source, self.pages.text(), target,
                         self.password, name=f"Removing pages from {self.source.name}")
        else:
            self.run_job(extract_pages, self.source, self.pages.text(), target,
                         self.password, separate_files=self._sep,
                         name=f"Extracting pages from {self.source.name}")

    def on_success(self, result) -> None:
        self.show_result(result.message, result.outputs,
                         f"{len(result.outputs)} file(s) written", result.warnings)
        self.record("Extract pages", self.source, result.output, result.pages,
                    self.pages.text())
        db.bump_stat("pdfs_processed")
        db.bump_stat("pages_processed", result.pages)
        self.finish_output(result.outputs, self.output)
        self.toast(result.message)
