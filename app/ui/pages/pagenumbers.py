"""Page Numbers."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QLineEdit

from app import db
from app.core import annotate
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class PageNumbersPage(SingleFileToolPage):
    TITLE = "Page Numbers"
    SUBTITLE = "Number the pages in any style"
    ACTION = "Add page numbers"
    OUTPUT_SUFFIX = "numbered"
    HISTORY_MODULE = "Page Numbers"

    def build(self) -> None:
        self.add_input_section()

        card = C.Card("Format")
        self.fmt = C.combo(
            [(k, annotate.NUMBER_FORMATS[k][0]) for k in annotate.NUMBER_FORMAT_ORDER],
            "page_n_of_m")
        self.fmt.currentIndexChanged.connect(self._update_preview)
        card.add(C.FormRow("Style", self.fmt))

        self.custom = QLineEdit()
        self.custom.setPlaceholderText("Leave blank to use the style above")
        self.custom.textChanged.connect(self._update_preview)
        card.add(C.FormRow(
            "Custom format", self.custom,
            "Use {page}, {total}, {roman}, {ROMAN}, {letter}. "
            "Example:  — {page} of {total} —"))

        self.preview = QLabel("")
        self.preview.setObjectName("Mono")
        card.add(self.preview)
        self.scroll.add(card)

        place = C.Card("Placement")
        self.position = C.PositionGrid("bottom-center")
        place.add(C.FormRow("Position", self.position))
        self.margin = C.dspin(30, 6, 150, " pt", 2, 1)
        place.add(C.FormRow("Distance from the edge", self.margin))
        self.font = C.combo([(f, f) for f in annotate.FONT_ORDER], "Helvetica")
        place.add(C.FormRow("Font", self.font))
        self.size = C.dspin(10, 5, 40, " pt", 0.5, 1)
        place.add(C.FormRow("Size", self.size))
        self.color = C.ColorPicker("#333333")
        place.add(C.FormRow("Colour", self.color))
        self.box = C.check("Draw a background box behind the number", False)
        self.box.toggled.connect(lambda on: self.box_color_row.setVisible(on))
        place.add(self.box)
        self.box_color = C.ColorPicker("#F0F0F0")
        self.box_color_row = C.FormRow("Box colour", self.box_color)
        self.box_color_row.setVisible(False)
        place.add(self.box_color_row)
        self.scroll.add(place)

        numbering = C.Card("Which pages, and starting from what")
        self.pages = C.PageRangeEdit(0, default="all")
        self.pages.textChanged.connect(self._update_preview)
        numbering.add(C.FormRow("Number these pages", self.pages))
        self.skip_first = C.check("Skip the first page", False,
                                  "Common when page 1 is a cover.")
        numbering.add(self.skip_first)
        self.start_at = C.spin(1, 0, 100000)
        self.start_at.valueChanged.connect(self._update_preview)
        numbering.add(C.FormRow("Start numbering at", self.start_at,
                                "The number printed on the first numbered page."))
        self.first_page = C.spin(1, 1, 100000)
        numbering.add(C.FormRow(
            "First page to carry a number", self.first_page,
            "Pages before this are left blank — useful for front matter."))
        self.scroll.add(numbering)

        self.add_output_section()
        self.add_run_section()
        self._update_preview()

    def on_source_changed(self) -> None:
        self.pages.set_total(self.page_count)
        self.first_page.setMaximum(max(1, self.page_count))
        self._update_preview()

    def _template(self) -> str:
        custom = self.custom.text().strip()
        return custom or annotate.NUMBER_FORMATS[self.fmt.currentData()][1]

    def _update_preview(self) -> None:
        total = self.page_count or 25
        template = self._template()
        start = self.start_at.value()
        samples = [annotate.substitute(template, page=start + i, total=total,
                                       filename="document.pdf") for i in range(3)]
        self.preview.setText("Preview:   " + "     ·     ".join(samples) + "   …")

    def validate(self) -> str:
        base = super().validate()
        if base:
            return base
        if not self.pages.pages():
            return "That page selection matches no pages."
        return ""

    def start(self) -> None:
        opts = annotate.PageNumberOptions(
            fmt=self.fmt.currentData(),
            custom=self.custom.text().strip(),
            position=self.position.value(),
            font=self.font.currentData(),
            font_size=self.size.value(),
            color=self.color.color(),
            margin=self.margin.value(),
            start_number=self.start_at.value(),
            first_numbered_page=self.first_page.value(),
            pages=self.pages.text(),
            skip_first=self.skip_first.isChecked(),
            box=self.box.isChecked(),
            box_color=self.box_color.color(),
        )
        target = self.output.resolved(self.source, "numbered")
        self.run_job(annotate.page_numbers, self.source, target, opts, self.password,
                     name=f"Numbering {self.source.name}")

    def on_success(self, result) -> None:
        self.show_result(result.message, [result.output] if result.output else [],
                         "", result.warnings)
        self.record("Page numbers", self.source, result.output, result.pages)
        db.bump_stat("pdfs_processed")
        self.finish_output([result.output] if result.output else [], self.output)
        self.toast(result.message)
