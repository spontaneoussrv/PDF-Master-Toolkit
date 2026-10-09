"""
The six conversion screens.

    PdfToWordPage · WordToPdfPage · PdfToExcelPage
    ExcelToPdfPage · PdfToImagesPage · ImagesToPdfPage

They share a common shape, so each one is small: describe the options, run the
matching engine, report which engine actually did the work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from app import db
from app.core import convert, deps, images, utils
from app.core.pdfbase import PAGE_SIZES, document, looks_scanned
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage, ToolPage

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif")


def _engine_banner() -> C.Banner | None:
    """Explain up front which Office conversion route is available."""
    if deps.has_msword() and deps.has_pywin32():
        return C.Banner(
            "Microsoft Word was detected and will be used, which gives the highest "
            "fidelity conversion.", "success")
    if deps.has_soffice():
        return C.Banner(
            "LibreOffice was detected and will be used. Microsoft Word is not "
            "installed; results are close but complex layouts may shift slightly.",
            "info")
    return C.Banner(
        "Neither Microsoft Word nor LibreOffice was found, so the built-in "
        "renderer will be used. It handles paragraphs, headings, simple tables and "
        "inline images — not columns, floating boxes or advanced formatting. "
        "Installing LibreOffice (free) markedly improves the result.", "warning")


# ===========================================================================
# PDF -> Word
# ===========================================================================

class PdfToWordPage(SingleFileToolPage):
    TITLE = "PDF to Word"
    SUBTITLE = "Create an editable .docx"
    ACTION = "Convert to Word"
    OUTPUT_SUFFIX = "converted"
    OUTPUT_EXT = ".docx"
    HISTORY_MODULE = "PDF to Word"

    def build(self) -> None:
        self.add_input_section()

        self.scan_banner = C.Banner("", "warning")
        self.scan_banner.setVisible(False)
        self.scroll.add(self.scan_banner)

        self.ocr_btn = QPushButton("Run OCR first")
        self.ocr_btn.setObjectName("Primary")
        self.ocr_btn.setVisible(False)
        self.ocr_btn.clicked.connect(self._send_to_ocr)
        self.scroll.add(self.ocr_btn)

        card = C.Card("Conversion")
        self.keep_layout = C.check(
            "Reconstruct the original layout", True,
            "Rebuilds columns, tables and image positions. Turn off for a simple "
            "top-to-bottom text document.")
        card.add(self.keep_layout)
        self.tables = C.check("Rebuild tables as Word tables", True)
        self.imgs = C.check("Include images", True)
        card.add(self.tables)
        card.add(self.imgs)

        self.pages = C.PageRangeEdit(0, default="all")
        card.add(C.FormRow("Pages", self.pages))
        self.scroll.add(card)

        if not deps.has_pdf2docx():
            self.scroll.add(C.Banner(
                "The layout-reconstruction component is not present in this build, "
                "so a simpler text-and-image conversion will be used.", "warning"))

        self.scroll.add(C.Banner(
            "PDF is a fixed-layout format, so no converter reproduces every "
            "document perfectly. Text, headings, images and simple tables convert "
            "well; heavily designed pages, multi-column magazines and unusual fonts "
            "may need tidying afterwards.", "info"))

        self.add_output_section()
        self.add_run_section()

    def on_source_changed(self) -> None:
        self.pages.set_total(self.page_count)
        try:
            with document(self.source, self.password) as doc:
                scanned = looks_scanned(doc)
        except Exception:
            scanned = False
        self.scan_banner.setVisible(scanned)
        self.ocr_btn.setVisible(scanned)
        if scanned:
            self.scan_banner.set_text(
                "This PDF looks scanned — it holds page images rather than text. "
                "Converting it now produces a Word file containing pictures, not "
                "editable words. Run OCR first for an editable result.")

    def _send_to_ocr(self) -> None:
        path = str(self.source)
        self.go("ocr")
        if self.window_ref:
            page = self.window_ref.current_page()
            if page and hasattr(page, "handle_files"):
                page.handle_files([path])

    def start(self) -> None:
        opts = convert.PdfToWordOptions(
            pages=self.pages.text(),
            keep_layout=self.keep_layout.isChecked(),
            extract_tables=self.tables.isChecked(),
            extract_images=self.imgs.isChecked(),
        )
        target = self.output.resolved(self.source, "converted", ".docx")
        self.run_job(convert.pdf_to_word, self.source, target, opts, self.password,
                     name=f"Converting {self.source.name}")

    def on_success(self, result) -> None:
        note = convert.FIDELITY_NOTE.get(result.fidelity, "")
        self.show_result(result.message, result.outputs,
                         f"Converted with {result.engine}." +
                         (f"\n{note}" if note else ""), result.warnings)
        self.record("PDF to Word", self.source, result.output, result.pages,
                    result.engine)
        db.bump_stat("files_converted")
        db.bump_stat("pdfs_processed")
        self.finish_output(result.outputs, self.output)
        self.toast("Word document created")


# ===========================================================================
# Word -> PDF
# ===========================================================================

class WordToPdfPage(SingleFileToolPage):
    TITLE = "Word to PDF"
    SUBTITLE = "Convert .docx documents to PDF"
    ACTION = "Convert to PDF"
    OUTPUT_SUFFIX = ""
    EXTENSIONS = (".docx", ".doc", ".rtf", ".odt")
    DROP_TEXT = "Drop a Word document here"
    HISTORY_MODULE = "Word to PDF"

    def build(self) -> None:
        self.add_input_section()
        banner = _engine_banner()
        if banner:
            self.scroll.add(banner)

        card = C.Card("Options")
        self.engine = C.combo(self._engine_options(), "auto")
        card.add(C.FormRow("Conversion engine", self.engine))
        self.bookmarks = C.check("Turn headings into PDF bookmarks", True)
        self.embed = C.check("Embed fonts", True)
        card.add(self.bookmarks)
        card.add(self.embed)
        self.scroll.add(card)

        self.add_output_section()
        self.add_run_section()

    def _engine_options(self) -> list[tuple]:
        opts = [("auto", "Automatic — best available")]
        if deps.has_msword() and deps.has_pywin32():
            opts.append(("word", "Microsoft Word"))
        if deps.has_soffice():
            opts.append(("libreoffice", "LibreOffice"))
        opts.append(("builtin", "Built-in renderer (basic)"))
        return opts

    def validate(self) -> str:
        if not self.source:
            return "Choose a Word document first."
        if self.source.suffix.lower() == ".doc" and not (
                deps.has_msword() or deps.has_soffice()):
            return ("Legacy .doc files need Microsoft Word or LibreOffice. "
                    "Save the file as .docx and try again.")
        return ""

    def start(self) -> None:
        opts = convert.WordToPdfOptions(
            keep_bookmarks=self.bookmarks.isChecked(),
            embed_fonts=self.embed.isChecked(),
            engine=self.engine.currentData(),
        )
        target = self.output.resolved(self.source, "", ".pdf")
        self.run_job(convert.word_to_pdf, self.source, target, opts,
                     name=f"Converting {self.source.name}")

    def on_success(self, result) -> None:
        self.show_result(result.message, result.outputs,
                         convert.FIDELITY_NOTE.get(result.fidelity, ""),
                         result.warnings)
        self.record("Word to PDF", self.source, result.output, result.pages,
                    result.engine)
        db.bump_stat("files_converted")
        self.finish_output(result.outputs, self.output)
        self.toast("PDF created")


# ===========================================================================
# PDF -> Excel
# ===========================================================================

class PdfToExcelPage(SingleFileToolPage):
    TITLE = "PDF to Excel"
    SUBTITLE = "Extract tables into a workbook"
    ACTION = "Find tables"
    OUTPUT_SUFFIX = "tables"
    OUTPUT_EXT = ".xlsx"
    HISTORY_MODULE = "PDF to Excel"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self.tables: list[convert.ExtractedTable] = []

    def build(self) -> None:
        self.add_input_section()

        card = C.Card("Detection")
        self.pages = C.PageRangeEdit(0, default="all")
        card.add(C.FormRow("Pages to scan", self.pages))
        self.min_rows = C.spin(2, 1, 200, " rows")
        self.min_cols = C.spin(2, 1, 100, " columns")
        card.add(C.FormRow("Ignore tables smaller than", self.min_rows))
        card.add(C.FormRow("…and narrower than", self.min_cols))
        self.scroll.add(card)

        out_card = C.Card("Export")
        self.fmt = C.SegmentedControl([("xlsx", "Excel workbook"), ("csv", "CSV files")],
                                      "xlsx")
        out_card.add(C.FormRow("Format", self.fmt))
        self.sheet_per_page = C.check("One sheet per table", True)
        out_card.add(self.sheet_per_page)
        self.scroll.add(out_card)

        self.scroll.add(C.Banner(
            "Table detection needs real text. If the PDF is a scan, run OCR first — "
            "otherwise there is nothing for the detector to read.", "info"))

        self.add_output_section()
        self.add_run_section("Find tables")

        self.preview_card = C.Card("Detected tables", "Review them before exporting.")
        self.table_list = QTableWidget(0, 4)
        self.table_list.setHorizontalHeaderLabels(["Page", "Table", "Size", "First row"])
        self.table_list.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table_list.setMinimumHeight(200)
        self.table_list.setSelectionBehavior(QTableWidget.SelectRows)
        self.preview_card.add(self.table_list)

        row = QHBoxLayout()
        self.export_btn = QPushButton("Export to Excel")
        self.export_btn.setObjectName("Primary")
        self.export_btn.clicked.connect(self._export)
        row.addWidget(self.export_btn)
        rescan = QPushButton("Scan again")
        rescan.setObjectName("Ghost")
        rescan.clicked.connect(self._on_run_clicked)
        row.addWidget(rescan)
        row.addStretch(1)
        self.preview_card.add_layout(row)
        self.preview_card.setVisible(False)
        self.scroll.add(self.preview_card)

    def on_source_changed(self) -> None:
        self.pages.set_total(self.page_count)
        self.preview_card.setVisible(False)
        self.tables = []

    def _options(self) -> convert.PdfToExcelOptions:
        return convert.PdfToExcelOptions(
            pages=self.pages.text(),
            fmt=self.fmt.value(),
            sheet_per_page=self.sheet_per_page.isChecked(),
            min_rows=self.min_rows.value(),
            min_cols=self.min_cols.value(),
        )

    def start(self) -> None:
        self.run_job(convert.find_tables, self.source, self._options(), self.password,
                     name=f"Scanning {self.source.name} for tables")

    def on_success(self, result) -> None:
        if isinstance(result, list):
            self._on_tables_found(result)
        else:
            self._on_exported(result)

    def _on_tables_found(self, tables) -> None:
        self.tables = tables
        self.table_list.setRowCount(len(tables))
        for i, t in enumerate(tables):
            rows, cols = t.shape
            first = " | ".join(str(c) for c in t.rows[0][:6])
            for col, value in enumerate([str(t.page), str(t.index),
                                         f"{rows} × {cols}", utils.elide(first, 70)]):
                self.table_list.setItem(i, col, QTableWidgetItem(value))
        self.table_list.resizeColumnsToContents()
        self.table_list.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)

        if not tables:
            self.preview_card.setVisible(False)
            self.show_result(
                "No tables detected", [],
                "Nothing matched the size filters. If this is a scanned document, "
                "run OCR first. Otherwise try lowering the minimum table size.")
            self.set_status("No tables detected", "warning")
            return

        self.preview_card.setVisible(True)
        self.export_btn.setText("Export to Excel" if self.fmt.value() == "xlsx"
                                else "Export to CSV")
        self.show_result(f"Found {len(tables)} table(s)", [],
                         "Review them above, then export.")
        self.toast(f"Found {len(tables)} table(s)")

    def _export(self) -> None:
        if not self.tables:
            self.toast("Scan for tables first", "warning")
            return
        ext = ".xlsx" if self.fmt.value() == "xlsx" else ".csv"
        target = self.output.resolved(self.source, "tables", ext)
        self.run_job(convert.tables_to_workbook, self.tables, target, self._options(),
                     name="Exporting tables")

    def _on_exported(self, result) -> None:
        self.show_result(result.message, result.outputs, "", result.warnings)
        self.record("PDF to Excel", self.source, result.output, 0,
                    f"{result.tables} tables")
        db.bump_stat("files_converted")
        self.finish_output(result.outputs, self.output)
        self.toast(result.message)


# ===========================================================================
# Excel -> PDF
# ===========================================================================

class ExcelToPdfPage(SingleFileToolPage):
    TITLE = "Excel to PDF"
    SUBTITLE = "Convert spreadsheets to PDF"
    ACTION = "Convert to PDF"
    OUTPUT_SUFFIX = ""
    EXTENSIONS = (".xlsx", ".xlsm", ".xls", ".csv")
    DROP_TEXT = "Drop a spreadsheet here"
    HISTORY_MODULE = "Excel to PDF"

    def build(self) -> None:
        self.add_input_section()

        self.sheets_card = C.Card("Sheets")
        self.all_sheets = C.check("Convert every sheet", True)
        self.all_sheets.toggled.connect(self._on_all_sheets)
        self.sheets_card.add(self.all_sheets)
        self.sheet_list = C.combo([], None)
        self.sheet_list.setEnabled(False)
        self.sheets_card.add(C.FormRow("Sheet", self.sheet_list))
        self.scroll.add(self.sheets_card)

        card = C.Card("Page setup")
        self.page_size = C.combo([(k, k) for k in
                                  ("A4", "A3", "Letter", "Legal", "Tabloid")], "A4")
        card.add(C.FormRow("Paper size", self.page_size))
        self.orientation = C.SegmentedControl(
            [("portrait", "Portrait"), ("landscape", "Landscape")], "landscape")
        card.add(C.FormRow("Orientation", self.orientation))
        self.margin = C.dspin(36, 0, 144, " pt", 2, 1)
        card.add(C.FormRow("Margins", self.margin))
        self.fit_width = C.check("Fit all columns onto the page width", True)
        self.gridlines = C.check("Draw cell borders", True)
        self.repeat_header = C.check("Repeat the first row on every page", True)
        card.add(self.fit_width)
        card.add(self.gridlines)
        card.add(self.repeat_header)
        self.scroll.add(card)

        if not (deps.has_msexcel() or deps.has_soffice()):
            self.scroll.add(C.Banner(
                "Neither Microsoft Excel nor LibreOffice was found. The built-in "
                "renderer will be used: cell values, column widths and borders are "
                "preserved, but charts, images and conditional formatting are not.",
                "warning"))

        self.add_output_section()
        self.add_run_section()

    def _on_all_sheets(self, on: bool) -> None:
        self.sheet_list.setEnabled(not on)

    def on_source_changed(self) -> None:
        self.sheet_list.clear()
        if not self.source or self.source.suffix.lower() == ".csv":
            self.sheets_card.setVisible(self.source is not None
                                        and self.source.suffix.lower() != ".csv")
            return
        self.sheets_card.setVisible(True)
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(self.source), read_only=True)
            for name in wb.sheetnames:
                self.sheet_list.addItem(name, name)
            wb.close()
            self.set_status(f"{self.sheet_list.count()} sheet(s) found")
        except Exception as e:
            self.set_status(f"Could not read the sheet list: {e}", "warning")

    def start(self) -> None:
        opts = convert.ExcelToPdfOptions(
            sheets=[] if self.all_sheets.isChecked() else
            ([self.sheet_list.currentData()] if self.sheet_list.currentData() else []),
            all_sheets=self.all_sheets.isChecked(),
            orientation=self.orientation.value(),
            page_size=self.page_size.currentData(),
            margin=self.margin.value(),
            fit_to_width=self.fit_width.isChecked(),
            gridlines=self.gridlines.isChecked(),
            repeat_header=self.repeat_header.isChecked(),
        )
        target = self.output.resolved(self.source, "", ".pdf")
        self.run_job(convert.excel_to_pdf, self.source, target, opts,
                     name=f"Converting {self.source.name}")

    def on_success(self, result) -> None:
        self.show_result(result.message, result.outputs,
                         convert.FIDELITY_NOTE.get(result.fidelity, ""),
                         result.warnings)
        self.record("Excel to PDF", self.source, result.output, result.pages,
                    result.engine)
        db.bump_stat("files_converted")
        self.finish_output(result.outputs, self.output)
        self.toast("PDF created")


# ===========================================================================
# PDF -> Images
# ===========================================================================

class PdfToImagesPage(SingleFileToolPage):
    TITLE = "PDF to Images"
    SUBTITLE = "Render pages as PNG, JPG, WEBP or TIFF"
    ACTION = "Export images"
    OUTPUT_SUFFIX = "images"
    HISTORY_MODULE = "PDF to Images"

    def build(self) -> None:
        self.add_input_section()

        card = C.Card("Image settings")
        self.fmt = C.combo([(k, v["label"]) for k, v in images.IMAGE_FORMATS.items()],
                           "png")
        self.fmt.currentIndexChanged.connect(self._on_format)
        card.add(C.FormRow("Format", self.fmt))

        self.dpi = C.combo([(d, f"{d} DPI" + (
            "  — screen" if d <= 96 else
            "  — recommended" if d == 150 else
            "  — print" if d == 300 else
            "  — very large files" if d >= 600 else "")) for d in images.DPI_PRESETS]
            + [(0, "Custom…")], 150)
        self.dpi.currentIndexChanged.connect(self._on_dpi)
        card.add(C.FormRow("Resolution", self.dpi))

        self.custom_dpi = C.spin(150, 24, 1200, " DPI")
        self.custom_dpi_row = C.FormRow("Custom resolution", self.custom_dpi)
        self.custom_dpi_row.setVisible(False)
        card.add(self.custom_dpi_row)

        self.quality = C.spin(90, 10, 100, "%")
        self.quality_row = C.FormRow("Quality", self.quality)
        self.quality_row.setVisible(False)
        card.add(self.quality_row)

        self.grayscale = C.check("Grayscale", False)
        self.transparent = C.check("Transparent background", False,
                                   "PNG and WEBP only.")
        card.add(self.grayscale)
        card.add(self.transparent)

        self.single_tiff = C.check("Save as one multi-page TIFF", False)
        self.single_tiff.setVisible(False)
        card.add(self.single_tiff)

        self.pages = C.PageRangeEdit(0, default="all")
        card.add(C.FormRow("Pages", self.pages))

        self.name_template = C.combo([
            ("{stem}_page{page:03d}", "document_page001.png"),
            ("{stem}-{page}", "document-1.png"),
            ("page{page:03d}", "page001.png"),
            ("{page:04d}", "0001.png"),
        ], "{stem}_page{page:03d}")
        card.add(C.FormRow("File names", self.name_template))

        self.size_hint = C.muted("")
        card.add(self.size_hint)
        self.dpi.currentIndexChanged.connect(self._update_hint)
        self.scroll.add(card)

        self.add_output_section(folder_mode=True)
        self.output.set_title("Output folder")
        self.add_run_section()

    def _on_format(self) -> None:
        fmt = self.fmt.currentData()
        self.quality_row.setVisible(fmt in ("jpg", "webp"))
        self.transparent.setEnabled(fmt in ("png", "webp"))
        self.single_tiff.setVisible(fmt == "tiff")
        self._update_hint()

    def _on_dpi(self) -> None:
        custom = self.dpi.currentData() == 0
        self.custom_dpi_row.setVisible(custom)
        self._update_hint()

    def _dpi_value(self) -> int:
        return self.custom_dpi.value() if self.dpi.currentData() == 0 else int(self.dpi.currentData())

    def _update_hint(self) -> None:
        if not self.page_count:
            self.size_hint.setText("")
            return
        dpi = self._dpi_value()
        n = len(self.pages.pages()) or self.page_count
        px_w = int(8.27 * dpi)
        px_h = int(11.7 * dpi)
        per = px_w * px_h * (3 if not self.grayscale.isChecked() else 1)
        if self.fmt.currentData() in ("jpg", "webp"):
            per *= 0.06
        elif self.fmt.currentData() == "png":
            per *= 0.22
        self.size_hint.setText(
            f"About {px_w} × {px_h} pixels per page — roughly "
            f"{utils.human_size(per * n)} in total for {n} page(s). This is an "
            f"estimate; actual size depends on the page content.")

    def on_source_changed(self) -> None:
        self.pages.set_total(self.page_count)
        if self.source:
            self.output.dir_picker.setText(
                str(self.source.parent / f"{self.source.stem}_images"))
        self._update_hint()

    def start(self) -> None:
        opts = images.RasterOptions(
            fmt=self.fmt.currentData(),
            dpi=self._dpi_value(),
            pages=self.pages.text(),
            quality=self.quality.value(),
            grayscale=self.grayscale.isChecked(),
            transparent=self.transparent.isChecked(),
            name_template=self.name_template.currentData(),
            single_tiff=self.single_tiff.isChecked(),
        )
        self.run_job(images.pdf_to_images, self.source, self.output.output_dir(),
                     opts, self.password,
                     name=f"Rendering {self.source.name}")

    def on_success(self, result) -> None:
        total = sum(utils.file_size(p) for p in result.outputs)
        self.show_result(result.message,
                         [result.folder] if result.folder else result.outputs,
                         f"{utils.human_size(total)} in {result.folder}",
                         result.warnings)
        self.record("PDF to Images", self.source, result.folder, result.pages,
                    f"{self._dpi_value()} DPI {self.fmt.currentData()}")
        db.bump_stat("files_converted")
        db.bump_stat("pages_processed", result.pages)
        if self.output.open_after.isChecked() and result.folder:
            utils.open_path(result.folder)
        self.toast(result.message)


# ===========================================================================
# Images -> PDF
# ===========================================================================

class ImagesToPdfPage(ToolPage):
    TITLE = "Images to PDF"
    SUBTITLE = "Build a PDF from photos or scans"
    ACTION = "Create PDF"
    HISTORY_MODULE = "Images to PDF"

    def build(self) -> None:
        card = C.Card("Images", "Add them, then drag the rows into the page order "
                                "you want.")
        self.files = C.FileListWidget(IMAGE_EXT)
        self.files.changed.connect(self._on_changed)
        card.add(self.files)

        row = QHBoxLayout()
        for text, slot in [("Sort by name", self.files.sort_by_name),
                           ("Sort by date", self.files.sort_by_date),
                           ("Reverse", self.files.reverse)]:
            b = QPushButton(text)
            b.setObjectName("ChipButton")
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        card.add_layout(row)
        self.scroll.add(card)

        page_card = C.Card("Page layout")
        self.page_size = C.combo(
            [("original", "Match each image's own size")] +
            [(k, k) for k in ("A4", "A5", "A3", "Letter", "Legal", "Tabloid")], "A4")
        self.page_size.currentIndexChanged.connect(self._on_page_size)
        page_card.add(C.FormRow("Page size", self.page_size))

        self.orientation = C.SegmentedControl([
            ("auto", "Match each image"),
            ("portrait", "Portrait"),
            ("landscape", "Landscape"),
        ], "auto")
        self.orientation_row = C.FormRow("Orientation", self.orientation)
        page_card.add(self.orientation_row)

        self.fit = C.combo([(k, v) for k, v in images.FIT_MODES.items()], "fit")
        self.fit_row = C.FormRow("How to place the image", self.fit)
        page_card.add(self.fit_row)

        self.margin = C.dspin(24, 0, 144, " pt", 2, 1)
        self.margin_row = C.FormRow("Margin", self.margin)
        page_card.add(self.margin_row)
        self.scroll.add(page_card)

        quality_card = C.Card("Image quality")
        self.quality = C.spin(88, 30, 98, "%")
        quality_card.add(C.FormRow(
            "JPEG quality", self.quality,
            "Higher keeps more detail and makes a larger PDF."))
        self.max_dim = C.combo([
            (0, "Keep the original resolution"),
            (3508, "Downscale to A4 at 300 DPI"),
            (2480, "Downscale to A4 at 200 DPI"),
            (1754, "Downscale to A4 at 150 DPI"),
            (1240, "Downscale to A4 at 100 DPI"),
        ], 0)
        quality_card.add(C.FormRow("Resolution limit", self.max_dim))
        self.grayscale = C.check("Convert to grayscale", False)
        quality_card.add(self.grayscale)
        self.scroll.add(quality_card)

        self.ocr_check = C.check(
            "Make the result searchable with OCR", False,
            "Recognises the text in each image and adds an invisible text layer.")
        self.ocr_check.setEnabled(deps.has_tesseract())
        if not deps.has_tesseract():
            self.ocr_check.setToolTip("The OCR engine is not available on this computer.")
        ocr_card = C.Card("Searchable output")
        ocr_card.add(self.ocr_check)
        self.scroll.add(ocr_card)

        self.output = C.OutputPanel("", ".pdf")
        self.output.name_edit.setText("images")
        self.scroll.add(self.output)

        self.add_run_section()
        self.run_bar.set_enabled(False)

    def _on_page_size(self) -> None:
        original = self.page_size.currentData() == "original"
        for row in (self.orientation_row, self.fit_row, self.margin_row):
            row.setVisible(not original)

    def _on_changed(self) -> None:
        n = self.files.count()
        if self.run_bar:
            self.run_bar.set_enabled(n > 0)
        if n:
            first = Path(self.files.paths()[0])
            self.output.set_source(first, "")
            if not self.output.name_edit.text().strip():
                self.output.name_edit.setText(first.stem)
            self.set_status(f"{n} image(s) — the PDF will have {n} page(s)")
        else:
            self.set_status("Add some images")

    def handle_files(self, paths: Sequence[str]) -> None:
        imgs = [p for p in paths if Path(p).suffix.lower() in IMAGE_EXT]
        if imgs:
            self.files.add_files(imgs)

    def validate(self) -> str:
        if self.files.count() == 0:
            return "Add at least one image."
        return ""

    def start(self) -> None:
        paths = [Path(p) for p in self.files.paths()]
        target = self.output.resolved(paths[0], "")
        self._target = target

        if self.ocr_check.isChecked():
            from app.core import ocr as ocr_mod
            opts = ocr_mod.OcrOptions.from_settings()
            opts.mode = "searchable"
            self.run_job(ocr_mod.ocr_images, paths, target, opts, make_pdf=True,
                         name=f"Building a searchable PDF from {len(paths)} image(s)")
            return

        opts = images.ImagesToPdfOptions(
            page_size=self.page_size.currentData(),
            landscape=self.orientation.value() == "landscape",
            auto_orient=self.orientation.value() == "auto",
            fit=self.fit.currentData(),
            margin=self.margin.value(),
            quality=self.quality.value(),
            grayscale=self.grayscale.isChecked(),
            max_dimension=int(self.max_dim.currentData()),
        )
        self.run_job(images.images_to_pdf, paths, target, opts,
                     name=f"Building a PDF from {len(paths)} image(s)")

    def on_success(self, result) -> None:
        outputs = result.outputs
        pages = getattr(result, "pages", 0) or getattr(result, "page_count", 0)
        size = sum(utils.file_size(p) for p in outputs)
        self.show_result(
            getattr(result, "message", "PDF created"), outputs,
            f"{pages} page(s) · {utils.human_size(size)}",
            getattr(result, "warnings", []))
        self.record("Images to PDF", Path(self.files.paths()[0]),
                    outputs[0] if outputs else None, pages,
                    f"{self.files.count()} images")
        db.bump_stat("files_converted")
        db.bump_stat("pages_processed", pages)
        if self.ocr_check.isChecked():
            db.bump_stat("ocr_documents")
        self.finish_output(outputs, self.output)
        self.toast("PDF created")
