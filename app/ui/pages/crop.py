"""Crop PDF — interactive crop with a live preview."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from app import db
from app.core import utils
from app.core.pdf_ops import auto_crop_margins, crop
from app.core.pdfbase import needs_password
from app.ui import components as C
from app.ui.croptool import CropCanvas
from app.ui.pages.base import ToolPage


class CropPage(ToolPage):
    TITLE = "Crop PDF"
    SUBTITLE = "Trim margins visually or by measurement"
    ACTION = "Apply crop"
    HISTORY_MODULE = "Crop"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self.source: Path | None = None
        self.password: str | None = None
        self._page_count = 0
        self._syncing = False

    def build(self) -> None:
        self._root.removeWidget(self.scroll)
        self.scroll.setParent(None)

        outer = QWidget()
        lay = QHBoxLayout(outer)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)

        # ---- left: canvas ------------------------------------------------
        left = QVBoxLayout()
        left.setSpacing(10)

        self.drop = C.DropZone("Drop a PDF here to crop it", hint="or click to browse",
                               compact=True)
        self.drop.files_dropped.connect(lambda p: self.load(p[0]))
        left.addWidget(self.drop)

        self.crop_canvas = CropCanvas()
        self.crop_canvas.crop_changed.connect(self._on_canvas_crop)
        self.crop_canvas.setVisible(False)
        left.addWidget(self.crop_canvas, 1)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Previous")
        self.prev_btn.setObjectName("Ghost")
        self.prev_btn.clicked.connect(
            lambda: self.crop_canvas.set_page(self.crop_canvas.page_index - 1))
        self.page_label = QLabel("")
        self.page_label.setObjectName("Subtle")
        self.next_btn = QPushButton("Next ▶")
        self.next_btn.setObjectName("Ghost")
        self.next_btn.clicked.connect(
            lambda: self.crop_canvas.set_page(self.crop_canvas.page_index + 1))
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.page_label, 1)
        nav.addWidget(self.next_btn)
        self.nav_holder = QWidget()
        self.nav_holder.setLayout(nav)
        self.nav_holder.setVisible(False)
        left.addWidget(self.nav_holder)
        lay.addLayout(left, 3)

        # ---- right: controls ---------------------------------------------
        side = C.ScrollArea(margins=(0, 0, 0, 0), spacing=14)
        side.setMaximumWidth(400)
        side.setMinimumWidth(330)

        card = C.Card("Crop area", "Drag the handles on the preview, or type exact "
                                   "margins in points (72 points = 1 inch).")
        self.left_spin = C.dspin(0, 0, 2000, " pt", 2, 1)
        self.top_spin = C.dspin(0, 0, 2000, " pt", 2, 1)
        self.right_spin = C.dspin(0, 0, 2000, " pt", 2, 1)
        self.bottom_spin = C.dspin(0, 0, 2000, " pt", 2, 1)
        for s in (self.left_spin, self.top_spin, self.right_spin, self.bottom_spin):
            s.valueChanged.connect(self._on_spin_changed)
        card.add(C.FormRow("Left margin", self.left_spin, label_width=110))
        card.add(C.FormRow("Top margin", self.top_spin, label_width=110))
        card.add(C.FormRow("Right margin", self.right_spin, label_width=110))
        card.add(C.FormRow("Bottom margin", self.bottom_spin, label_width=110))

        row = QHBoxLayout()
        auto = QPushButton("Detect content")
        auto.setObjectName("ChipButton")
        auto.setToolTip("Set the crop to the page's ink bounds")
        auto.clicked.connect(self._auto_detect)
        reset = QPushButton("Reset")
        reset.setObjectName("ChipButton")
        reset.clicked.connect(self._reset)
        row.addWidget(auto)
        row.addWidget(reset)
        row.addStretch(1)
        card.add_layout(row)
        side.add(card)

        apply_card = C.Card("Apply to")
        self.scope = C.SegmentedControl([
            ("all", "All pages"),
            ("current", "This page"),
            ("custom", "Selected pages"),
        ], "all")
        self.scope.changed.connect(self._on_scope)
        apply_card.add(self.scope)
        self.pages = C.PageRangeEdit(0, default="all")
        self.pages_row = C.FormRow("Pages", self.pages, label_width=70)
        self.pages_row.setVisible(False)
        apply_card.add(self.pages_row)

        self.auto_each = C.check(
            "Detect content separately on every page", False,
            "Useful for scans where the content sits differently on each page. "
            "The manual crop area is ignored when this is on.")
        self.auto_each.toggled.connect(self._on_auto_each)
        apply_card.add(self.auto_each)
        self.padding = C.dspin(6, 0, 120, " pt", 1, 1)
        self.padding_row = C.FormRow("Padding to keep", self.padding, label_width=130)
        self.padding_row.setVisible(False)
        apply_card.add(self.padding_row)
        side.add(apply_card)

        side.add(C.Banner(
            "Cropping changes the visible page box. The hidden content is still "
            "inside the file — use Redact to remove content permanently.", "info"))

        self.output = C.OutputPanel("cropped", ".pdf")
        side.add(self.output)

        self.result_panel = C.ResultPanel()
        side.add(self.result_panel)

        self.run_bar = C.RunBar("Apply crop")
        self.run_bar.run_clicked.connect(self._on_run_clicked)
        self.run_bar.cancel_clicked.connect(self.cancel)
        self.run_bar.set_enabled(False)
        side.add(self.run_bar)
        side.add_stretch()

        lay.addWidget(side, 2)
        self._root.addWidget(outer, 1)

        self.crop_canvas.page_loaded.connect(self._on_page_loaded)

    # -- loading -----------------------------------------------------------
    def handle_files(self, paths: Sequence[str]) -> None:
        for p in paths:
            if str(p).lower().endswith(".pdf"):
                self.load(p)
                return

    def receive(self, payload) -> None:
        if isinstance(payload, (str, Path)):
            self.load(payload)

    def load(self, path: str | Path) -> None:
        p = Path(path)
        if not p.is_file():
            return
        password = None
        if needs_password(p):
            from PySide6.QtWidgets import QInputDialog, QLineEdit
            from app.core.pdfbase import open_pdf
            pw, ok = QInputDialog.getText(
                self, "Password required",
                f"'{p.name}' is password protected.\nEnter the password:",
                QLineEdit.Password)
            if not ok:
                return
            try:
                d = open_pdf(p, pw)
                d.close()
                password = pw
            except Exception:
                self.toast("That password was not accepted", "error")
                return

        if not self.crop_canvas.load(p, password):
            self.toast("This PDF could not be opened", "error")
            return
        self.source = p
        self.password = password
        self.drop.setVisible(False)
        self.crop_canvas.setVisible(True)
        self.nav_holder.setVisible(True)
        self.output.set_source(p, "cropped")
        self.run_bar.set_enabled(True)
        self._sync_spins()

    def _on_page_loaded(self, index: int, count: int) -> None:
        self._page_count = count
        self.pages.set_total(count)
        self.page_label.setText(f"Page {index + 1} of {count}")
        self.prev_btn.setEnabled(index > 0)
        self.next_btn.setEnabled(index < count - 1)

    # -- crop sync ---------------------------------------------------------
    def _on_canvas_crop(self, _box) -> None:
        self._sync_spins()

    def _sync_spins(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        l, t, r, b = self.crop_canvas.margins_points()
        self.left_spin.setValue(l)
        self.top_spin.setValue(t)
        self.right_spin.setValue(r)
        self.bottom_spin.setValue(b)
        self._syncing = False

    def _on_spin_changed(self) -> None:
        if self._syncing or self.source is None:
            return
        self._syncing = True
        self.crop_canvas.set_margins_points(
            self.left_spin.value(), self.top_spin.value(),
            self.right_spin.value(), self.bottom_spin.value())
        self._syncing = False

    def _auto_detect(self) -> None:
        if self.source is None:
            return
        if self.crop_canvas.auto_detect(self.padding.value() or 6.0):
            self.set_status("Crop set to the detected content area")
        else:
            self.set_status("No content was detected on this page", "warning")

    def _reset(self) -> None:
        self.crop_canvas.reset_crop()

    def _on_scope(self, value: str) -> None:
        self.pages_row.setVisible(value == "custom")

    def _on_auto_each(self, on: bool) -> None:
        self.padding_row.setVisible(on)
        for s in (self.left_spin, self.top_spin, self.right_spin, self.bottom_spin):
            s.setEnabled(not on)

    # -- run ---------------------------------------------------------------
    def _pages_spec(self) -> str:
        scope = self.scope.value()
        if scope == "current":
            return str(self.crop_canvas.page_index + 1)
        if scope == "custom":
            return self.pages.text()
        return "all"

    def validate(self) -> str:
        if not self.source:
            return "Open a PDF first."
        if not utils.parse_page_ranges(self._pages_spec(), self._page_count):
            return "That page selection matches no pages."
        if not self.auto_each.isChecked():
            l, t, r, b = self.crop_canvas.crop()
            if (r - l) < 0.03 or (b - t) < 0.03:
                return "The crop area is too small."
            if (l, t, r, b) == (0.0, 0.0, 1.0, 1.0):
                return "Nothing to crop — the area covers the whole page."
        return ""

    def start(self) -> None:
        target = self.output.resolved(self.source, "cropped")
        spec = self._pages_spec()
        if self.auto_each.isChecked():
            self.run_job(auto_crop_margins, self.source, target,
                         self.padding.value(), spec, self.password,
                         name=f"Auto-cropping {self.source.name}")
        else:
            self.run_job(crop, self.source, target,
                         relative_box=self.crop_canvas.crop(),
                         pages_spec=spec, password=self.password,
                         name=f"Cropping {self.source.name}")

    def on_success(self, result) -> None:
        self.show_result(result.message, result.outputs, "", result.warnings)
        self.record("Crop", self.source, result.output, result.pages)
        db.bump_stat("pdfs_processed")
        self.finish_output(result.outputs, self.output)
        self.toast(result.message)
