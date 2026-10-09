"""Split PDF."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QStackedWidget, QWidget, QVBoxLayout

from app import db
from app.core import utils
from app.core.pdf_ops import split
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage

MODES = [
    ("every_page", "Every page", "One file per page."),
    ("every_n", "Every N pages", "Fixed-size chunks, for example every 10 pages."),
    ("into_n", "Into N equal parts", "Divide the document into a set number of files."),
    ("ranges", "Custom ranges", "One output file per line, e.g. 1-5 then 6-10."),
    ("selected", "Selected pages only", "Take just the pages you list."),
    ("before_page", "Split before a page", "Two files, cut before the page you choose."),
    ("after_page", "Split after a page", "Two files, cut after the page you choose."),
    ("by_size", "By file size", "Fill each file up to a size limit."),
    ("by_bookmarks", "By bookmarks", "One file per top-level bookmark."),
]


class SplitPage(SingleFileToolPage):
    TITLE = "Split PDF"
    SUBTITLE = "Break a PDF into smaller files"
    ACTION = "Split PDF"
    OUTPUT_SUFFIX = "split"
    HISTORY_MODULE = "Split"

    def build(self) -> None:
        self.add_input_section("The original file is never modified.")

        card = C.Card("How should it be split?")
        self.mode = C.combo([(k, t) for k, t, _d in MODES], "every_page")
        self.mode.currentIndexChanged.connect(self._on_mode)
        card.add(C.FormRow("Method", self.mode))

        self.mode_hint = QLabel(MODES[0][2])
        self.mode_hint.setObjectName("Subtle")
        self.mode_hint.setWordWrap(True)
        card.add(self.mode_hint)

        self.stack = QStackedWidget()
        self.stack.addWidget(QWidget())                                    # every_page
        self.every_n = C.spin(10, 1, 5000, " pages")
        self.stack.addWidget(self._wrap(C.FormRow("Pages per file", self.every_n)))
        self.parts = C.spin(2, 2, 500, " files")
        self.stack.addWidget(self._wrap(C.FormRow("Number of files", self.parts)))

        self.ranges = QPlainTextEdit()
        self.ranges.setPlaceholderText("1-5\n6-10\n11-20")
        self.ranges.setFixedHeight(96)
        self.stack.addWidget(self._wrap(C.FormRow(
            "Ranges (one file per line)", self.ranges,
            "Each line becomes one output file. A line may hold several ranges: 1-3,8,12-14")))

        self.selected = C.PageRangeEdit(0, default="1-5")
        self.stack.addWidget(self._wrap(C.FormRow("Pages to keep", self.selected)))

        self.before_page = C.spin(2, 1, 100000)
        self.stack.addWidget(self._wrap(C.FormRow("Split before page", self.before_page)))
        self.after_page = C.spin(1, 1, 100000)
        self.stack.addWidget(self._wrap(C.FormRow("Split after page", self.after_page)))

        self.max_mb = C.dspin(10.0, 0.1, 4096.0, " MB", 0.5, 2)
        self.stack.addWidget(self._wrap(C.FormRow(
            "Maximum size per file", self.max_mb,
            "Pages are added until the next one would exceed this limit. "
            "A single page larger than the limit becomes its own file.")))

        self.bookmark_level = C.spin(1, 1, 6)
        self.stack.addWidget(self._wrap(C.FormRow(
            "Bookmark level", self.bookmark_level,
            "Level 1 is the top of the outline.")))

        card.add(self.stack)
        self.scroll.add(card)

        naming = C.Card("File names")
        self.template = C.combo([
            ("{stem}_part{index}", "document_part1.pdf"),
            ("{stem}_{first}-{last}", "document_1-5.pdf"),
            ("{stem}_{label}", "document_1-5.pdf (from the range text)"),
            ("part{index}", "part1.pdf"),
        ], "{stem}_part{index}")
        naming.add(C.FormRow("Pattern", self.template))
        self.scroll.add(naming)

        self.add_output_section(folder_mode=True)
        self.output.set_title("Output folder")
        self.add_run_section()

    def _wrap(self, widget) -> QWidget:
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.addWidget(widget)
        return holder

    def _on_mode(self) -> None:
        idx = self.mode.currentIndex()
        self.stack.setCurrentIndex(idx)
        self.mode_hint.setText(MODES[idx][2])
        if MODES[idx][0] == "by_bookmarks" and self.source:
            from app.core.pdfbase import document
            try:
                with document(self.source, self.password) as doc:
                    n = len(doc.get_toc(simple=True) or [])
                self.set_status(f"This document has {n} bookmark(s)" if n
                                else "This document has no bookmarks")
            except Exception:
                pass

    def on_source_changed(self) -> None:
        self.selected.set_total(self.page_count)
        for spin in (self.before_page, self.after_page):
            spin.setMaximum(max(1, self.page_count))
        if self.source:
            self.output.dir_picker.setText(
                str(self.source.parent / f"{self.source.stem}_split"))
        self.set_status(f"{self.page_count} pages ready to split")

    def validate(self) -> str:
        base = super().validate()
        if base:
            return base
        mode = self.mode.currentData()
        if mode == "ranges" and not self.ranges.toPlainText().strip():
            return "Enter at least one page range."
        if mode == "selected" and not self.selected.pages():
            return "That page selection matches no pages."
        return ""

    def start(self) -> None:
        mode = self.mode.currentData()
        self.run_job(
            split, self.source, mode,
            password=self.password,
            every_n=self.every_n.value(),
            ranges=self.ranges.toPlainText(),
            selected=self.selected.text(),
            at_page=(self.before_page.value() if mode == "before_page"
                     else self.after_page.value()),
            max_mb=self.max_mb.value(),
            bookmark_level=self.bookmark_level.value(),
            parts=self.parts.value(),
            out_dir=self.output.output_dir(),
            name_template=self.template.currentData(),
            name=f"Splitting {self.source.name}",
        )

    def on_success(self, result) -> None:
        folder = result.outputs[0].parent if result.outputs else None
        sizes = ", ".join(utils.human_size(utils.file_size(p)) for p in result.outputs[:4])
        self.show_result(
            f"Created {len(result.outputs)} file(s)",
            result.outputs,
            f"Saved in {folder}" + (f"\nSizes: {sizes}…" if len(result.outputs) > 4
                                    else f"\nSizes: {sizes}"),
            result.warnings,
        )
        self.record("Split", self.source, folder, result.pages,
                    f"{len(result.outputs)} parts")
        db.bump_stat("pdfs_processed")
        db.bump_stat("pages_processed", result.pages)
        if self.output.open_after.isChecked() and folder:
            utils.open_path(folder)
        self.toast(f"Split into {len(result.outputs)} files")
