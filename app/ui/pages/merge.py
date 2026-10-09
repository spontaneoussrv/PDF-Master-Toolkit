"""Merge PDF."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

from app import db
from app.core import utils
from app.core.pdf_ops import MergeItem, merge
from app.ui import components as C
from app.ui.pages.base import ToolPage


class MergePage(ToolPage):
    TITLE = "Merge PDF"
    SUBTITLE = "Combine several PDFs into one"
    ACTION = "Merge PDFs"
    HISTORY_MODULE = "Merge"

    def build(self) -> None:
        card = C.Card(
            "Documents to merge",
            "Add files, drag the rows to set the order, and optionally choose "
            "which pages to take from each one.")
        self.files = C.FileListWidget(show_ranges=True)
        self.files.changed.connect(self._on_files_changed)
        self.files.open_requested.connect(lambda p: self.go("viewer", p))
        card.add(self.files)

        sort_row = QHBoxLayout()
        for text, slot in [("Sort by name", self.files.sort_by_name),
                           ("Sort by date", self.files.sort_by_date),
                           ("Reverse", self.files.reverse)]:
            b = QPushButton(text)
            b.setObjectName("ChipButton")
            b.clicked.connect(slot)
            sort_row.addWidget(b)
        sort_row.addStretch(1)
        card.add_layout(sort_row)
        self.scroll.add(card)

        opts = C.Card("Options")
        self.bookmark_check = C.check(
            "Add a bookmark for each source file", True,
            "Creates a navigable outline so you can jump to each original document.")
        self.keep_toc_check = C.check(
            "Keep the bookmarks from the source files", True)
        opts.add(self.bookmark_check)
        opts.add(self.keep_toc_check)
        self.scroll.add(opts)

        self.output = C.OutputPanel("merged", ".pdf")
        self.output.name_edit.setText("merged")
        self.scroll.add(self.output)

        self.add_run_section()
        self.run_bar.set_enabled(False)

    # -- state -------------------------------------------------------------
    def _on_files_changed(self) -> None:
        n = self.files.count()
        if self.run_bar is not None:
            self.run_bar.set_enabled(n >= 1)
        if n and self.files.paths():
            first = Path(self.files.paths()[0])
            self.output.set_source(first, "")
            if not self.output.name_edit.text().strip():
                self.output.name_edit.setText(f"{first.stem}_merged")
        if n == 0:
            self.set_status("Add at least two PDFs to merge")
        elif n == 1:
            self.set_status("Add one more file — a merge needs at least two")
        else:
            pages = self.files.total_pages()
            self.set_status(f"{n} files ready" + (f" · about {pages} pages" if pages else ""))

    def handle_files(self, paths: Sequence[str]) -> None:
        pdfs = [p for p in paths if str(p).lower().endswith(".pdf")]
        if pdfs:
            self.files.add_files(pdfs)

    def validate(self) -> str:
        if self.files.count() < 1:
            return "Add at least one PDF."
        if self.files.count() < 2:
            return "A merge needs at least two files."
        return ""

    # -- run ---------------------------------------------------------------
    def start(self) -> None:
        items: list[MergeItem] = []
        for path in self.files.paths():
            items.append(MergeItem(Path(path), pages=self.files.range_for(path)))

        target = self.output.resolved(Path(items[0].path), "merged")
        self._target = target
        self.run_job(
            merge, items, target,
            bookmark_per_file=self.bookmark_check.isChecked(),
            keep_bookmarks=self.keep_toc_check.isChecked(),
            name=f"Merging {len(items)} files",
        )

    def on_success(self, result) -> None:
        out = result.output
        self.show_result(
            f"Merged into {result.pages} pages — {utils.human_size(result.total_size)}",
            result.outputs,
            f"Saved as {out.name}" if out else "",
            result.warnings,
        )
        self.record("Merge", Path(self.files.paths()[0]), out, result.pages,
                    f"{self.files.count()} files")
        db.bump_stat("pdfs_processed", self.files.count())
        db.bump_stat("pages_processed", result.pages)
        self.finish_output(result.outputs, self.output)
        self.toast(f"Merged {self.files.count()} files")
