"""Rotate Pages."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QPushButton

from app import db
from app.core.pdf_ops import rotate
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class RotatePage(SingleFileToolPage):
    TITLE = "Rotate Pages"
    SUBTITLE = "Turn pages 90, 180 or 270 degrees"
    ACTION = "Rotate pages"
    OUTPUT_SUFFIX = "rotated"
    HISTORY_MODULE = "Rotate"

    def build(self) -> None:
        self.add_input_section()

        card = C.Card("Rotation")
        self.angle = C.SegmentedControl([
            (90, "90° clockwise"),
            (180, "180°"),
            (270, "90° anticlockwise"),
        ], "90")
        card.add(C.FormRow("Turn by", self.angle))

        self.absolute = C.check(
            "Set the rotation instead of adding to it", False,
            "By default the angle is added to each page's existing rotation. "
            "Tick this to force every selected page to the exact angle.")
        card.add(self.absolute)

        self.pages = C.PageRangeEdit(0, default="all")
        card.add(C.FormRow("Apply to", self.pages))

        chips = QHBoxLayout()
        for text, value in [("All pages", "all"), ("Odd pages", "odd"),
                            ("Even pages", "even"), ("First page", "1"),
                            ("Last page", "last")]:
            b = QPushButton(text)
            b.setObjectName("ChipButton")
            b.clicked.connect(lambda _=False, v=value: self.pages.setText(v))
            chips.addWidget(b)
        chips.addStretch(1)
        card.add_layout(chips)

        card.add(C.muted(
            "Rotation is stored as a page attribute, so nothing is re-rendered "
            "and text stays selectable."))
        self.scroll.add(card)

        self.add_output_section()
        self.add_run_section()

    def on_source_changed(self) -> None:
        self.pages.set_total(self.page_count)

    def validate(self) -> str:
        base = super().validate()
        if base:
            return base
        if not self.pages.pages():
            return "That page selection matches no pages."
        return ""

    def start(self) -> None:
        target = self.output.resolved(self.source, "rotated")
        self.run_job(rotate, self.source, int(self.angle.value()), target,
                     self.pages.text(), self.password,
                     absolute=self.absolute.isChecked(),
                     name=f"Rotating {self.source.name}")

    def on_success(self, result) -> None:
        self.show_result(result.message, result.outputs, "", result.warnings)
        self.record("Rotate", self.source, result.output, result.pages,
                    f"{self.angle.value()}°")
        db.bump_stat("pdfs_processed")
        self.finish_output(result.outputs, self.output)
        self.toast(result.message)
