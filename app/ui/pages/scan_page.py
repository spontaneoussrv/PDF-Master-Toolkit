"""Scan to PDF — isolated so a missing or misbehaving scanner cannot destabilise the app."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap, QTransform
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QListView,
                               QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from app import db
from app.core import deps, scanner, utils
from app.ui import components as C
from app.ui.pages.base import ToolPage


class ScanPage(ToolPage):
    TITLE = "Scan to PDF"
    SUBTITLE = "Scan paper directly into a searchable PDF"
    ACTION = "Scan"
    HISTORY_MODULE = "Scan"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self.pages: list[scanner.ScannedPage] = []

    def build(self) -> None:
        available = scanner.is_available()

        if not available:
            self.scroll.add(C.Banner(scanner.unavailable_reason(), "warning"))
            alt = C.Card("What you can do instead")
            alt.add(QLabel(
                "Scan with the software that came with your scanner, save the pages "
                "as images or a PDF, then bring them here."))
            row = QHBoxLayout()
            for key, text in [("img2pdf", "Images to PDF"), ("ocr", "OCR a scanned PDF"),
                              ("organize", "Organise pages")]:
                b = QPushButton(text)
                b.setObjectName("ChipButton")
                b.clicked.connect(lambda _=False, k=key: self.go(k))
                row.addWidget(b)
            row.addStretch(1)
            alt.add_layout(row)
            self.scroll.add(alt)

        device_card = C.Card("Scanner")
        self.device = C.combo([], None)
        device_card.add(C.FormRow("Device", self.device))
        row = QHBoxLayout()
        refresh = QPushButton("Look for scanners")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self._refresh_devices)
        row.addWidget(refresh)
        row.addStretch(1)
        device_card.add_layout(row)
        self.device_note = C.muted("")
        device_card.add(self.device_note)
        self.scroll.add(device_card)

        settings = C.Card("Scan settings")
        self.dpi = C.combo([(150, "150 DPI — drafts"),
                            (200, "200 DPI"),
                            (300, "300 DPI — recommended for OCR"),
                            (400, "400 DPI"),
                            (600, "600 DPI — small print, slow")], 300)
        settings.add(C.FormRow("Resolution", self.dpi))
        self.color = C.SegmentedControl([("color", "Colour"),
                                         ("grayscale", "Grayscale"),
                                         ("blackwhite", "Black and white")], "color")
        settings.add(C.FormRow("Mode", self.color))
        self.source_mode = C.SegmentedControl([("flatbed", "Flatbed glass"),
                                               ("feeder", "Document feeder")], "flatbed")
        settings.add(C.FormRow("Paper source", self.source_mode))
        self.duplex = C.check("Scan both sides (feeder only)", False)
        settings.add(self.duplex)
        self.page_size = C.combo([(k, k) for k in
                                  ("A4", "A5", "Letter", "Legal")], "A4")
        settings.add(C.FormRow("Page size", self.page_size))
        self.brightness = C.spin(0, -1000, 1000)
        settings.add(C.FormRow("Brightness", self.brightness))
        self.contrast = C.spin(0, -1000, 1000)
        settings.add(C.FormRow("Contrast", self.contrast))
        self.scroll.add(settings)

        self.pages_card = C.Card("Scanned pages",
                                 "Drag to reorder. Right-click for more options.")
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setIconSize(QSize(120, 160))
        self.grid.setGridSize(QSize(140, 194))
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setSpacing(6)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setDragDropMode(QAbstractItemView.InternalMove)
        self.grid.setMinimumHeight(220)
        self.pages_card.add(self.grid)

        tools = QHBoxLayout()
        for text, slot in [("Rotate left", lambda: self._rotate(-90)),
                           ("Rotate right", lambda: self._rotate(90)),
                           ("Delete", self._delete),
                           ("Clear all", self._clear)]:
            b = QPushButton(text)
            b.setObjectName("ChipButton")
            b.clicked.connect(slot)
            tools.addWidget(b)
        tools.addStretch(1)
        self.pages_card.add_layout(tools)
        self.pages_card.setVisible(False)
        self.scroll.add(self.pages_card)

        finish = C.Card("Save as PDF")
        self.run_ocr = C.check(
            "Make the PDF searchable with OCR", deps.has_tesseract(),
            "Recognises the text on each scanned page.")
        self.run_ocr.setEnabled(deps.has_tesseract())
        finish.add(self.run_ocr)
        self.deskew = C.check("Straighten crooked pages", True)
        finish.add(self.deskew)
        self.scroll.add(finish)

        self.output = C.OutputPanel("scan", ".pdf")
        self.output.name_edit.setText("scan")
        self.scroll.add(self.output)

        self.add_run_section("Start scanning")

        save_card = C.Card("")
        self.save_btn = QPushButton("Save scanned pages as PDF")
        self.save_btn.setObjectName("PrimaryLarge")
        self.save_btn.clicked.connect(self._save)
        save_card.add(self.save_btn)
        save_card.setVisible(False)
        self.save_card = save_card
        self.scroll.add(save_card)

        self._refresh_devices()
        if not available:
            self.run_bar.set_enabled(False)

    # -- devices -----------------------------------------------------------
    def _refresh_devices(self) -> None:
        self.device.clear()
        devices = scanner.list_devices()
        for d in devices:
            label = f"{d.name}" + (f"  ({d.manufacturer})" if d.manufacturer else "")
            self.device.addItem(label, d.device_id)
        if devices:
            self.device_note.setText(f"{len(devices)} scanner(s) found.")
            if self.run_bar:
                self.run_bar.set_enabled(True)
        else:
            self.device_note.setText(
                "No scanner detected. Check that it is switched on and connected, "
                "then press “Look for scanners” again.")
            if self.run_bar:
                self.run_bar.set_enabled(False)

    # -- scanning ----------------------------------------------------------
    def validate(self) -> str:
        if not scanner.is_available():
            return scanner.unavailable_reason()
        if self.device.count() == 0:
            return "No scanner is available."
        return ""

    def start(self) -> None:
        opts = scanner.ScanOptions(
            dpi=int(self.dpi.currentData()),
            color_mode=self.color.value(),
            source=self.source_mode.value(),
            duplex=self.duplex.isChecked(),
            brightness=self.brightness.value(),
            contrast=self.contrast.value(),
            page_size=self.page_size.currentData(),
        )
        self.run_job(scanner.scan, self.device.currentData(), opts,
                     name="Scanning")

    def on_success(self, result) -> None:
        if isinstance(result, scanner.ScanResult):
            self.pages.extend(result.pages)
            self._refresh_grid()
            self.show_result(result.message, [], "Review the pages, then save as PDF.",
                             result.warnings)
            self.toast(result.message)
        else:
            self._after_save(result)

    # -- page grid ---------------------------------------------------------
    def _refresh_grid(self) -> None:
        self.grid.clear()
        for i, page in enumerate(self.pages):
            item = QListWidgetItem(f"Page {i + 1}")
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignBottom)
            item.setSizeHint(QSize(134, 190))
            pm = QPixmap(str(page.path))
            if not pm.isNull():
                if page.rotation:
                    pm = pm.transformed(QTransform().rotate(page.rotation),
                                        Qt.SmoothTransformation)
                item.setIcon(pm.scaled(118, 156, Qt.KeepAspectRatio,
                                       Qt.SmoothTransformation))
            self.grid.addItem(item)
        has = bool(self.pages)
        self.pages_card.setVisible(has)
        self.save_card.setVisible(has)

    def _selected_rows(self) -> list[int]:
        return sorted(self.grid.row(i) for i in self.grid.selectedItems())

    def _rotate(self, angle: int) -> None:
        for r in self._selected_rows():
            self.pages[r].rotation = (self.pages[r].rotation + angle) % 360
        self._refresh_grid()

    def _delete(self) -> None:
        for r in reversed(self._selected_rows()):
            del self.pages[r]
        self._refresh_grid()

    def _clear(self) -> None:
        if self.pages and not self.confirm(
                "Discard scanned pages?",
                f"All {len(self.pages)} scanned page(s) will be discarded.",
                destructive=True):
            return
        self.pages.clear()
        self._refresh_grid()

    # -- saving ------------------------------------------------------------
    def _save(self) -> None:
        if not self.pages:
            self.toast("There is nothing to save", "warning")
            return
        # honour the order the user dragged the thumbnails into
        order = []
        for row in range(self.grid.count()):
            order.append(self.pages[row])
        self.pages = order

        target = self.output.resolved(self.pages[0].path, "", ".pdf")
        if self.run_ocr.isChecked() and deps.has_tesseract():
            from app.core import ocr as ocr_mod
            opts = ocr_mod.OcrOptions.from_settings()
            opts.mode = "searchable"
            opts.deskew = self.deskew.isChecked()
            files = [p.path for p in self.pages if not p.deleted]
            self.run_job(ocr_mod.ocr_images, files, target, opts, make_pdf=True,
                         name="Building a searchable PDF")
        else:
            self.run_job(scanner.pages_to_pdf, self.pages, target,
                         self.page_size.currentData(),
                         name="Building the PDF")

    def _after_save(self, result) -> None:
        outputs = result.outputs if hasattr(result, "outputs") else [result]
        outputs = [Path(o) for o in outputs if o]
        self.show_result(f"Saved {len(self.pages)} page(s) as PDF", outputs, "")
        self.record("Scan to PDF", None, outputs[0] if outputs else None,
                    len(self.pages))
        db.bump_stat("pdfs_processed")
        if self.run_ocr.isChecked():
            db.bump_stat("ocr_documents")
        self.finish_output(outputs, self.output)
        self.toast("Scan saved")
