"""Resize Pages — normalise every page onto a uniform sheet."""

from __future__ import annotations

from app import db
from app.core.pdf_ops import n_up, normalize_page_size
from app.core.pdfbase import PAGE_SIZES
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class PageSizePage(SingleFileToolPage):
    TITLE = "Resize Pages"
    SUBTITLE = "Scale every page to A4, Letter or another size"
    ACTION = "Resize pages"
    OUTPUT_SUFFIX = "a4"
    HISTORY_MODULE = "Resize"

    def build(self) -> None:
        self.add_input_section(
            "Useful when a document mixes page sizes — after merging scans, for "
            "example, or before printing.")

        card = C.Card("Target page")
        self.page_size = C.combo(
            [(k, f"{k}  ({int(v.width)} × {int(v.height)} pt)")
             for k, v in PAGE_SIZES.items()], "A4")
        self.page_size.currentIndexChanged.connect(self._update_suffix)
        card.add(C.FormRow("Page size", self.page_size))

        self.orientation = C.combo([
            ("auto", "Match each page's own orientation"),
            ("portrait", "Portrait for every page"),
            ("landscape", "Landscape for every page"),
            ("match", "Match the first page"),
        ], "auto")
        card.add(C.FormRow("Orientation", self.orientation))

        self.mode = C.SegmentedControl([
            ("fit", "Fit inside"),
            ("fill", "Fill (crop overflow)"),
            ("stretch", "Stretch"),
        ], "fit")
        card.add(C.FormRow(
            "Scaling", self.mode,
            "Fit keeps the whole page visible and adds white space; Fill covers the "
            "sheet and may cut the edges; Stretch distorts to fit exactly."))

        self.margin = C.dspin(0, 0, 144, " pt", 2, 1)
        card.add(C.FormRow("Margin", self.margin,
                           "White space around the scaled page. 72 points = 1 inch."))
        self.scroll.add(card)

        nup = C.Card("Multiple pages per sheet", "Optional — print several pages on one.")
        self.nup_enabled = C.check("Place several pages on each sheet", False)
        self.nup_enabled.toggled.connect(self._on_nup)
        nup.add(self.nup_enabled)
        self.nup_layout = C.combo([
            ((2, 1), "2 pages side by side"),
            ((1, 2), "2 pages stacked"),
            ((2, 2), "4 pages (2 × 2)"),
            ((3, 2), "6 pages (3 × 2)"),
            ((4, 2), "8 pages (4 × 2)"),
            ((3, 3), "9 pages (3 × 3)"),
        ], (2, 1))
        self.nup_row = C.FormRow("Layout", self.nup_layout)
        self.nup_row.setVisible(False)
        nup.add(self.nup_row)
        self.nup_gap = C.dspin(8, 0, 72, " pt", 1, 1)
        self.gap_row = C.FormRow("Gap between pages", self.nup_gap)
        self.gap_row.setVisible(False)
        nup.add(self.gap_row)
        self.scroll.add(nup)

        self.add_output_section()
        self.add_run_section()

    def _on_nup(self, on: bool) -> None:
        self.nup_row.setVisible(on)
        self.gap_row.setVisible(on)
        self.mode.setEnabled(not on)
        if self.run_bar:
            self.run_bar.set_action_text("Create layout" if on else "Resize pages")

    def _update_suffix(self) -> None:
        size = self.page_size.currentData()
        self.OUTPUT_SUFFIX = str(size).lower()
        if self.source and self.output:
            self.output.name_edit.setText(f"{self.source.stem}_{self.OUTPUT_SUFFIX}")

    def on_source_changed(self) -> None:
        self._update_suffix()
        if self.info and self.info.page_size:
            self.set_status(f"Current first page: {self.info.page_size}")

    def start(self) -> None:
        size = self.page_size.currentData()
        target = self.output.resolved(self.source, str(size).lower())
        if self.nup_enabled.isChecked():
            cols, rows = self.nup_layout.currentData()
            self.run_job(n_up, self.source, target, cols, rows, size,
                         landscape=(self.orientation.currentData() == "landscape"
                                    or cols >= rows),
                         gap=self.nup_gap.value(), margin=self.margin.value() or 18,
                         password=self.password,
                         name=f"Laying out {self.source.name}")
        else:
            self.run_job(normalize_page_size, self.source, target,
                         page_size=size,
                         orientation=self.orientation.currentData(),
                         mode=self.mode.value(),
                         margin=self.margin.value(),
                         password=self.password,
                         name=f"Resizing {self.source.name}")

    def on_success(self, result) -> None:
        self.show_result(result.message, result.outputs, "", result.warnings)
        self.record("Resize pages", self.source, result.output, result.pages,
                    str(self.page_size.currentData()))
        db.bump_stat("pdfs_processed")
        db.bump_stat("pages_processed", result.pages)
        self.finish_output(result.outputs, self.output)
        self.toast(result.message)
