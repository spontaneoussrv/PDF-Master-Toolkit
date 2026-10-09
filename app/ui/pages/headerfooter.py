"""Header & Footer."""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QLineEdit, QWidget

from app import db
from app.core import annotate
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage


class HeaderFooterPage(SingleFileToolPage):
    TITLE = "Header & Footer"
    SUBTITLE = "Add running headers and footers"
    ACTION = "Add header and footer"
    OUTPUT_SUFFIX = "headed"
    HISTORY_MODULE = "Header & Footer"

    def build(self) -> None:
        self.add_input_section()

        header_card = C.Card("Header", "Left, centre and right. Leave a box empty to "
                                       "skip that position.")
        self.header_left = QLineEdit()
        self.header_center = QLineEdit()
        self.header_right = QLineEdit()
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, (lbl, edit) in enumerate([("Left", self.header_left),
                                         ("Centre", self.header_center),
                                         ("Right", self.header_right)]):
            grid.addWidget(QLabel(lbl), 0, i)
            grid.addWidget(edit, 1, i)
            edit.textChanged.connect(self._update_preview)
        holder = QWidget()
        holder.setLayout(grid)
        header_card.add(holder)
        self.scroll.add(header_card)

        footer_card = C.Card("Footer")
        self.footer_left = QLineEdit()
        self.footer_center = QLineEdit("Page {page} of {total}")
        self.footer_right = QLineEdit()
        grid2 = QGridLayout()
        grid2.setSpacing(8)
        for i, (lbl, edit) in enumerate([("Left", self.footer_left),
                                         ("Centre", self.footer_center),
                                         ("Right", self.footer_right)]):
            grid2.addWidget(QLabel(lbl), 0, i)
            grid2.addWidget(edit, 1, i)
            edit.textChanged.connect(self._update_preview)
        holder2 = QWidget()
        holder2.setLayout(grid2)
        footer_card.add(holder2)
        self.scroll.add(footer_card)

        vars_card = C.Card("Variables", "Type these anywhere in a box and they are "
                                        "replaced on each page.")
        vgrid = QGridLayout()
        vgrid.setSpacing(6)
        for i, (token, desc) in enumerate(annotate.VARIABLE_HELP):
            code = QLabel(token)
            code.setObjectName("Mono")
            code.setCursor(code.cursor())
            vgrid.addWidget(code, i // 2, (i % 2) * 2)
            d = QLabel(desc)
            d.setObjectName("Subtle")
            vgrid.addWidget(d, i // 2, (i % 2) * 2 + 1)
        vholder = QWidget()
        vholder.setLayout(vgrid)
        vars_card.add(vholder)
        self.scroll.add(vars_card)

        style = C.Card("Style and placement")
        self.font = C.combo([(f, f) for f in annotate.FONT_ORDER], "Helvetica")
        style.add(C.FormRow("Font", self.font))
        self.size = C.dspin(9, 5, 24, " pt", 0.5, 1)
        style.add(C.FormRow("Size", self.size))
        self.color = C.ColorPicker("#444444")
        style.add(C.FormRow("Colour", self.color))
        self.margin = C.dspin(28, 6, 120, " pt", 2, 1)
        style.add(C.FormRow("Distance from the edge", self.margin))
        self.rule = C.check("Draw a thin rule under the header and above the footer",
                            False)
        style.add(self.rule)
        self.odd_even = C.check(
            "Mirror left and right on facing pages", False,
            "For double-sided printing: the left and right boxes swap on even pages.")
        style.add(self.odd_even)
        self.scroll.add(style)

        pages_card = C.Card("Pages and numbering")
        self.pages = C.PageRangeEdit(0, default="all")
        pages_card.add(C.FormRow("Apply to", self.pages))
        self.skip_first = C.check("Skip the first page", False)
        pages_card.add(self.skip_first)
        self.start_number = C.spin(1, 0, 100000)
        pages_card.add(C.FormRow("First page number", self.start_number,
                                 "What {page} shows on the first numbered page."))
        self.scroll.add(pages_card)

        preview_card = C.Card("Preview", "How the first page will read.")
        self.preview = QLabel("")
        self.preview.setObjectName("Mono")
        self.preview.setWordWrap(True)
        preview_card.add(self.preview)
        self.scroll.add(preview_card)

        self.add_output_section()
        self.add_run_section()
        self._update_preview()

    def on_source_changed(self) -> None:
        self.pages.set_total(self.page_count)
        self._update_preview()

    def _update_preview(self) -> None:
        total = self.page_count or 25
        name = self.source.name if self.source else "document.pdf"

        def show(template: str) -> str:
            return annotate.substitute(template, page=1, total=total, filename=name)

        header = [show(e.text()) for e in
                  (self.header_left, self.header_center, self.header_right)]
        footer = [show(e.text()) for e in
                  (self.footer_left, self.footer_center, self.footer_right)]
        lines = []
        if any(header):
            lines.append("Header:   " + "     |     ".join(h or "—" for h in header))
        if any(footer):
            lines.append("Footer:   " + "     |     ".join(f or "—" for f in footer))
        self.preview.setText("\n".join(lines) or "Nothing will be added yet.")

    def validate(self) -> str:
        base = super().validate()
        if base:
            return base
        boxes = [self.header_left, self.header_center, self.header_right,
                 self.footer_left, self.footer_center, self.footer_right]
        if not any(b.text().strip() for b in boxes):
            return "Enter text in at least one header or footer box."
        return ""

    def start(self) -> None:
        opts = annotate.HeaderFooterOptions(
            header_left=self.header_left.text(),
            header_center=self.header_center.text(),
            header_right=self.header_right.text(),
            footer_left=self.footer_left.text(),
            footer_center=self.footer_center.text(),
            footer_right=self.footer_right.text(),
            font=self.font.currentData(),
            font_size=self.size.value(),
            color=self.color.color(),
            margin=self.margin.value(),
            pages=self.pages.text(),
            skip_first=self.skip_first.isChecked(),
            start_number=self.start_number.value(),
            different_odd_even=self.odd_even.isChecked(),
            draw_rule=self.rule.isChecked(),
        )
        target = self.output.resolved(self.source, "headed")
        self.run_job(annotate.header_footer, self.source, target, opts, self.password,
                     name=f"Adding headers to {self.source.name}")

    def on_success(self, result) -> None:
        self.show_result(result.message, [result.output] if result.output else [],
                         "", result.warnings)
        self.record("Header & Footer", self.source, result.output, result.pages)
        db.bump_stat("pdfs_processed")
        self.finish_output([result.output] if result.output else [], self.output)
        self.toast(result.message)
