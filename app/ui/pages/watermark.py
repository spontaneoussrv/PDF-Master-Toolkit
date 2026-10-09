"""Watermark."""

from __future__ import annotations

from PySide6.QtWidgets import (QHBoxLayout, QLineEdit, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from app import db
from app.core import annotate
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class WatermarkPage(SingleFileToolPage):
    TITLE = "Watermark"
    SUBTITLE = "Stamp text or a logo across the pages"
    ACTION = "Add watermark"
    OUTPUT_SUFFIX = "watermarked"
    HISTORY_MODULE = "Watermark"

    def build(self) -> None:
        self.add_input_section()

        kind_card = C.Card("Watermark type")
        self.kind = C.SegmentedControl([("text", "Text"), ("image", "Image or logo")],
                                       "text")
        self.kind.changed.connect(self._on_kind)
        kind_card.add(self.kind)

        self.stack = QStackedWidget()

        # --- text ---------------------------------------------------------
        text_panel = QWidget()
        tl = QVBoxLayout(text_panel)
        tl.setContentsMargins(0, 8, 0, 0)
        tl.setSpacing(11)

        self.text = QLineEdit("CONFIDENTIAL")
        tl.addWidget(C.FormRow("Text", self.text))

        presets = QHBoxLayout()
        for word in annotate.WATERMARK_PRESETS:
            b = QPushButton(word)
            b.setObjectName("ChipButton")
            b.clicked.connect(lambda _=False, w=word: self.text.setText(w))
            presets.addWidget(b)
        presets.addStretch(1)
        holder = QWidget()
        holder.setLayout(presets)
        tl.addWidget(holder)

        self.font = C.combo([(f, f) for f in annotate.FONT_ORDER], "Helvetica Bold")
        tl.addWidget(C.FormRow("Font", self.font))

        self.auto_size = C.check("Size the text to span the page", True)
        self.auto_size.toggled.connect(lambda on: self.font_size.setEnabled(not on))
        tl.addWidget(self.auto_size)
        self.font_size = C.spin(60, 6, 400, " pt")
        self.font_size.setEnabled(False)
        tl.addWidget(C.FormRow("Font size", self.font_size))

        self.color = C.ColorPicker("#FF0000")
        tl.addWidget(C.FormRow("Colour", self.color))
        self.stack.addWidget(text_panel)

        # --- image --------------------------------------------------------
        image_panel = QWidget()
        il = QVBoxLayout(image_panel)
        il.setContentsMargins(0, 8, 0, 0)
        il.setSpacing(11)
        self.image_path = C.PathPicker(
            "open", "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)",
            "Choose a logo or stamp image")
        il.addWidget(C.FormRow("Image file", self.image_path))
        self.scale = C.dspin(0.55, 0.05, 1.0, "", 0.05, 2)
        il.addWidget(C.FormRow("Width, as a fraction of the page", self.scale))
        il.addWidget(C.muted(
            "A PNG with a transparent background gives the cleanest watermark."))
        self.stack.addWidget(image_panel)

        kind_card.add(self.stack)
        self.scroll.add(kind_card)

        # --- appearance ---------------------------------------------------
        look = C.Card("Appearance")
        self.opacity = C.dspin(0.18, 0.02, 1.0, "", 0.02, 2)
        look.add(C.FormRow("Opacity", self.opacity,
                           "0.15 to 0.25 stays readable without hiding the content."))
        self.rotation = C.dspin(45, -180, 180, "°", 5, 0)
        look.add(C.FormRow("Rotation", self.rotation))
        self.position = C.PositionGrid("center")
        look.add(C.FormRow("Position", self.position))
        self.margin = C.dspin(36, 0, 200, " pt", 2, 1)
        look.add(C.FormRow("Margin from the edge", self.margin))

        self.behind = C.check(
            "Place behind the page content", True,
            "Keeps the document readable. Turn off to lay the mark over the top.")
        look.add(self.behind)
        self.tile = C.check("Repeat across the whole page", False)
        self.tile.toggled.connect(lambda on: self.tile_gap_row.setVisible(on))
        look.add(self.tile)
        self.tile_gap = C.dspin(220, 60, 900, " pt", 10, 0)
        self.tile_gap_row = C.FormRow("Spacing between marks", self.tile_gap)
        self.tile_gap_row.setVisible(False)
        look.add(self.tile_gap_row)
        self.scroll.add(look)

        pages_card = C.Card("Pages")
        self.pages = C.PageRangeEdit(0, default="all")
        pages_card.add(C.FormRow("Apply to", self.pages))
        self.skip_first = C.check("Skip the first page", False,
                                  "Useful when the first page is a cover.")
        pages_card.add(self.skip_first)
        self.scroll.add(pages_card)

        self.add_output_section()
        self.add_run_section()

    def _on_kind(self, value: str) -> None:
        self.stack.setCurrentIndex(0 if value == "text" else 1)

    def on_source_changed(self) -> None:
        self.pages.set_total(self.page_count)

    def validate(self) -> str:
        base = super().validate()
        if base:
            return base
        if self.kind.value() == "text" and not self.text.text().strip():
            return "Enter the watermark text."
        if self.kind.value() == "image" and not self.image_path.path():
            return "Choose a watermark image."
        if not self.pages.pages():
            return "That page selection matches no pages."
        return ""

    def start(self) -> None:
        opts = annotate.WatermarkOptions(
            kind=self.kind.value(),
            text=self.text.text(),
            image_path=self.image_path.path(),
            font=self.font.currentData(),
            font_size=self.font_size.value(),
            auto_size=self.auto_size.isChecked(),
            color=self.color.color(),
            opacity=self.opacity.value(),
            rotation=self.rotation.value(),
            position=self.position.value(),
            margin=self.margin.value(),
            scale=self.scale.value(),
            behind=self.behind.isChecked(),
            tile=self.tile.isChecked(),
            tile_gap=self.tile_gap.value(),
            pages=self.pages.text(),
            skip_first=self.skip_first.isChecked(),
        )
        target = self.output.resolved(self.source, "watermarked")
        self.run_job(annotate.watermark, self.source, target, opts, self.password,
                     name=f"Watermarking {self.source.name}")

    def on_success(self, result) -> None:
        self.show_result(result.message, [result.output] if result.output else [],
                         "", result.warnings)
        self.record("Watermark", self.source, result.output, result.pages,
                    self.text.text() if self.kind.value() == "text" else "image")
        db.bump_stat("pdfs_processed")
        self.finish_output([result.output] if result.output else [], self.output)
        self.toast(result.message)
