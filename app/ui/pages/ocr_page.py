"""OCR PDF — the flagship recognition screen."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from app import config, db
from app.core import deps, ocr, utils
from app.core.pdfbase import document, looks_scanned
from app.ui import components as C
from app.ui.pages.base import SingleFileToolPage

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")


class OcrPage(SingleFileToolPage):
    TITLE = "OCR PDF"
    SUBTITLE = "Make scanned documents searchable"
    ACTION = "Run OCR"
    OUTPUT_SUFFIX = "ocr"
    EXTENSIONS = (".pdf",) + IMAGE_EXT
    DROP_TEXT = "Drop a scanned PDF or image here"
    HISTORY_MODULE = "OCR"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self.image_files: list[Path] = []
        self._result = None

    # ------------------------------------------------------------------ ui
    def build(self) -> None:
        self.setup_card = self._build_setup_card()
        self.scroll.add(self.setup_card)
        self.setup_card.setVisible(not deps.has_tesseract())

        self.add_input_section(
            "Works on scanned PDFs and on image files. The original page image is "
            "kept exactly as it is — the recognised text is added as an invisible "
            "layer underneath it.")

        self.images_card = C.Card("Image files", "These will become one searchable PDF.")
        self.image_list = C.FileListWidget(IMAGE_EXT)
        self.image_list.changed.connect(self._on_images_changed)
        self.images_card.add(self.image_list)
        self.images_card.setVisible(False)
        self.scroll.add(self.images_card)

        self.scan_banner = C.Banner("", "info")
        self.scan_banner.setVisible(False)
        self.scroll.add(self.scan_banner)

        self.scroll.add(self._build_language_card())
        self.scroll.add(self._build_mode_card())
        self.scroll.add(self._build_quality_card())

        self.add_output_section()
        self.add_run_section()

        self.text_card = C.Card("Recognised text",
                                "Review it here, then export or copy.")
        self.text_view = QPlainTextEdit()
        self.text_view.setMinimumHeight(240)
        self.text_view.setPlaceholderText("Recognised text appears here after OCR.")
        self.text_card.add(self.text_view)

        row = QHBoxLayout()
        for text, slot in [("Copy to clipboard", self._copy_text),
                           ("Save as TXT", lambda: self._export("txt")),
                           ("Save as Word", lambda: self._export("docx")),
                           ("Save as JSON", lambda: self._export("json")),
                           ("Save as CSV", lambda: self._export("csv"))]:
            b = QPushButton(text)
            b.setObjectName("ChipButton")
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        self.text_card.add_layout(row)
        self.text_card.setVisible(False)
        self.scroll.add(self.text_card)

    # ------------------------------------------------------------ OCR setup
    def _build_setup_card(self) -> C.Card:
        """
        Shown when Tesseract is absent.

        A dead-end error message is not much use, so this offers the three
        things that actually resolve it: get the engine, point at an existing
        copy, or re-check after installing.
        """
        card = C.Card(
            "OCR engine not installed",
            "Recognition needs Tesseract, a free open-source OCR engine. Every "
            "other tool in the application works without it.")

        card.add(C.Banner(
            "This copy of PDF Master Toolkit was built without the bundled OCR "
            "component, and Tesseract is not installed on this computer.",
            "warning"))

        steps = QLabel(
            "<b>Quickest fix — about two minutes:</b><br><br>"
            "1. Click <b>Download Tesseract OCR</b> below.<br>"
            "2. On that page, download the latest <b>64-bit</b> installer "
            "(<code>tesseract-ocr-w64-setup-….exe</code>).<br>"
            "3. Run it. When it offers <b>Additional language data</b>, expand "
            "it and tick the languages you need — English, Hindi, Bengali, "
            "Spanish, French, German, Italian, Portuguese — plus "
            "<b>Orientation and script detection</b> (needed for auto-rotate).<br>"
            "4. Come back here and click <b>Check again</b>.")
        steps.setWordWrap(True)
        steps.setTextFormat(Qt.RichText)
        card.add(steps)

        row = QHBoxLayout()
        row.setSpacing(8)

        download = QPushButton("Download Tesseract OCR")
        download.setObjectName("Primary")
        download.clicked.connect(self._open_download)
        row.addWidget(download)

        recheck = QPushButton("Check again")
        recheck.clicked.connect(self._recheck_engine)
        row.addWidget(recheck)

        locate = QPushButton("Locate it myself…")
        locate.setObjectName("Ghost")
        locate.setToolTip("Choose tesseract.exe if you installed it somewhere unusual")
        locate.clicked.connect(self._locate_engine)
        row.addWidget(locate)

        settings = QPushButton("OCR settings")
        settings.setObjectName("Ghost")
        settings.clicked.connect(lambda: self.go("settings"))
        row.addWidget(settings)
        row.addStretch(1)
        card.add_layout(row)

        self.setup_status = QLabel("")
        self.setup_status.setObjectName("Subtle")
        self.setup_status.setWordWrap(True)
        card.add(self.setup_status)

        card.add(C.muted(
            "Building this yourself? Install Tesseract on the build machine and "
            "run build.bat again — it now bundles the engine into the installer "
            "automatically, so the people you distribute to need no setup at all."))
        return card

    def _open_download(self) -> None:
        QDesktopServices.openUrl(QUrl(deps.TESSERACT_DOWNLOAD_URL))
        self.setup_status.setText(
            "Opened the download page in your browser. Install Tesseract, then "
            "press “Check again”.")

    def _recheck_engine(self) -> None:
        deps.refresh()
        found = deps.tesseract_path()
        if found:
            self._on_engine_found(found)
        else:
            self.setup_status.setText(
                "Still not found. If you have just installed it, make sure the "
                "installation finished, then press “Check again”. If you chose a "
                "custom folder, use “Locate it myself…”.")

    def _locate_engine(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        pattern = "tesseract.exe" if sys.platform == "win32" else "tesseract"
        path, _ = QFileDialog.getOpenFileName(
            self, "Find tesseract.exe",
            r"C:\Program Files" if sys.platform == "win32" else str(Path.home()),
            f"Tesseract ({pattern});;Programs (*.exe);;All files (*.*)")
        if not path:
            return
        config.set("tesseract_path", path)
        deps.refresh()
        found = deps.tesseract_path()
        if found:
            self._on_engine_found(found)
        else:
            self.setup_status.setText(
                f"“{Path(path).name}” did not work as an OCR engine. Choose the "
                f"tesseract.exe inside your Tesseract-OCR folder.")

    def _on_engine_found(self, path: str) -> None:
        langs = deps.installed_languages()
        self.setup_card.setVisible(False)
        self._populate_languages()
        self._on_langs_changed()
        names = ", ".join(deps.language_label(l) for l in langs[:8]) or "none detected"
        self.set_status(f"OCR engine ready — {len(langs)} language pack(s): {names}",
                        "success")
        self.toast("OCR engine found — you can run recognition now")

    def _build_language_card(self) -> C.Card:
        card = C.Card("Languages",
                      "Choose every language that appears in the document. "
                      "Selecting several is supported — for example English + Bengali.")
        self.lang_list = QListWidget()
        self.lang_list.setSelectionMode(QListWidget.MultiSelection)
        self.lang_list.setMaximumHeight(190)
        self._populate_languages()
        card.add(self.lang_list)

        row = QHBoxLayout()
        self.lang_summary = QLabel("")
        self.lang_summary.setObjectName("Subtle")
        self.lang_summary.setWordWrap(True)
        row.addWidget(self.lang_summary, 1)
        refresh = QPushButton("Refresh list")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self._refresh_languages)
        row.addWidget(refresh)
        card.add_layout(row)
        self.lang_list.itemSelectionChanged.connect(self._on_langs_changed)
        self._on_langs_changed()
        return card

    def _populate_languages(self) -> None:
        self.lang_list.clear()
        installed = set(deps.installed_languages())
        saved = set(config.get_list("ocr_languages", ["eng"]))

        codes = list(deps.PRIORITY_LANGS)
        codes += sorted(c for c in installed if c not in codes and c not in ("osd", "equ"))
        for code in utils.dedupe(codes):
            available = code in installed or not installed
            label = deps.language_label(code)
            item = QListWidgetItem(f"{label}   ({code})" +
                                   ("" if available else "   — not installed"))
            item.setData(Qt.UserRole, code)
            if not available:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                item.setToolTip(
                    f"The {label} language pack is not installed. Add "
                    f"{code}.traineddata to the tessdata folder shown in "
                    f"Settings → OCR.")
            self.lang_list.addItem(item)
            if code in saved and available:
                item.setSelected(True)

        if not self.selected_languages() and self.lang_list.count():
            first = self.lang_list.item(0)
            if first.flags() & Qt.ItemIsEnabled:
                first.setSelected(True)

    def _refresh_languages(self) -> None:
        deps.refresh()
        self._populate_languages()
        self.toast("Language list refreshed")

    def selected_languages(self) -> list[str]:
        return [i.data(Qt.UserRole) for i in self.lang_list.selectedItems()]

    def _on_langs_changed(self) -> None:
        langs = self.selected_languages()
        if not langs:
            self.lang_summary.setText("Select at least one language.")
            return
        names = ", ".join(deps.language_label(l) for l in langs)
        note = ""
        if len(langs) > 3:
            note = "  Recognition slows down with many languages — pick only those present."
        self.lang_summary.setText(f"Recognising: {names}{note}")

    def _build_mode_card(self) -> C.Card:
        card = C.Card("What to produce")
        self.mode = C.SegmentedControl([
            ("searchable", "Searchable PDF"),
            ("text", "Extract text only"),
            ("both", "Both"),
        ], config.get("ocr_mode", "searchable"))
        self.mode.changed.connect(self._on_mode)
        card.add(self.mode)

        self.mode_hint = C.muted(
            "The page looks exactly as it does now, but Ctrl+F finds words and text "
            "can be selected and copied.")
        card.add(self.mode_hint)

        self.pages = C.PageRangeEdit(0, default="all")
        card.add(C.FormRow("Pages to recognise", self.pages))

        self.skip_text = C.check(
            "Skip pages that already contain text", True,
            "Saves a lot of time on documents that are only partly scanned.")
        card.add(self.skip_text)
        return card

    def _on_mode(self, value: str) -> None:
        self.mode_hint.setText({
            "searchable": "The page looks exactly as it does now, but Ctrl+F finds "
                          "words and text can be selected and copied.",
            "text": "No PDF is written — the recognised text is shown below for "
                    "review and export.",
            "both": "Produces a searchable PDF and shows the text for export.",
        }[value])
        if self.output:
            self.output.setVisible(value != "text")

    def _build_quality_card(self) -> C.Card:
        card = C.Card("Scan quality", "Preprocessing applied before recognition. "
                                      "These change what the engine reads, never your original file.")
        self.dpi = C.combo([(150, "150 DPI — fastest"),
                            (200, "200 DPI"),
                            (300, "300 DPI — recommended"),
                            (400, "400 DPI"),
                            (600, "600 DPI — small print, slowest")],
                           config.get_int("ocr_dpi", 300))
        card.add(C.FormRow("Recognition resolution", self.dpi))

        grid = QGridLayout()
        grid.setSpacing(8)
        self.deskew = C.check("Straighten crooked scans", config.get_bool("ocr_deskew", True),
                              "Detects and corrects a tilted page before reading it.")
        self.autorotate = C.check("Correct page orientation",
                                  config.get_bool("ocr_rotate", True),
                                  "Fixes pages scanned upside down or sideways.")
        self.grayscale = C.check("Convert to grayscale",
                                 config.get_bool("ocr_grayscale", True))
        self.denoise = C.check("Reduce speckle noise", config.get_bool("ocr_denoise", False),
                               "Helps with photocopies and fax-quality scans.")
        self.contrast = C.check("Enhance contrast", config.get_bool("ocr_contrast", False),
                                "Helps with faint or washed-out print.")
        self.binarize = C.check("Convert to pure black and white",
                                config.get_bool("ocr_binarize", False),
                                "Strong clean-up for text-only documents. "
                                "Can lose detail in photographs.")
        for i, cb in enumerate((self.deskew, self.autorotate, self.grayscale,
                                self.denoise, self.contrast, self.binarize)):
            grid.addWidget(cb, i // 2, i % 2)
        holder = QWidget()
        holder.setLayout(grid)
        card.add(holder)

        self.psm = C.combo([
            (3, "Automatic — mixed text and images"),
            (1, "Automatic with orientation detection"),
            (4, "A single column of text"),
            (6, "A single uniform block of text"),
            (11, "Sparse text — find whatever is there"),
            (12, "Sparse text with orientation detection"),
        ], config.get_int("ocr_psm", 3))
        card.add(C.FormRow("Page layout", self.psm))

        self.engine = C.combo([
            ("auto", "Automatic — best for the chosen languages"),
            ("native", "Keep the original page exactly (invisible text layer)"),
            ("rebuild", "Rebuild pages from the OCR image (widest script support)"),
        ], "auto")
        card.add(C.FormRow(
            "Text layer", self.engine,
            "Automatic keeps your original page untouched whenever a suitable font "
            "is available, and only rebuilds when a script needs it."))
        return card

    # -- lifecycle ---------------------------------------------------------
    def on_show(self) -> None:
        """Re-probe on every visit: the user may have installed Tesseract since
        this page was built, and should not have to restart the application."""
        card = getattr(self, "setup_card", None)
        if card is None or not card.isVisible():
            return
        deps.refresh()
        found = deps.tesseract_path()
        if found:
            self._on_engine_found(found)

    # -- source ------------------------------------------------------------
    def handle_files(self, paths: Sequence[str]) -> None:
        pdfs = [p for p in paths if str(p).lower().endswith(".pdf")]
        imgs = [p for p in paths if Path(p).suffix.lower() in IMAGE_EXT]
        if pdfs:
            self.set_source(pdfs[0])
        if imgs:
            self.image_list.add_files(imgs)
            self.images_card.setVisible(True)
            if self.drop:
                self.drop.setVisible(False)

    def set_source(self, path) -> None:
        p = Path(path)
        if p.suffix.lower() in IMAGE_EXT:
            self.image_list.add_files([p])
            self.images_card.setVisible(True)
            if self.drop:
                self.drop.setVisible(False)
            return
        super().set_source(path)
        self.images_card.setVisible(False)
        self.image_list.clear()

    def _on_images_changed(self) -> None:
        n = self.image_list.count()
        self.images_card.setVisible(n > 0)
        if n:
            self.source = None
            self.scan_banner.setVisible(False)
            first = Path(self.image_list.paths()[0])
            self.output.set_source(first, "ocr")
            self.set_status(f"{n} image(s) ready — they will become one searchable PDF")

    def on_source_changed(self) -> None:
        self.pages.set_total(self.page_count)
        if not self.source:
            return
        try:
            with document(self.source, self.password) as doc:
                scanned = looks_scanned(doc)
                from app.core.pdfbase import document_text_ratio
                ratio = document_text_ratio(doc)
        except Exception:
            scanned, ratio = True, 0.0

        if scanned:
            self.scan_banner.set_kind("info")
            self.scan_banner.set_text(
                "This document appears to be scanned — OCR will make it searchable.")
        else:
            self.scan_banner.set_kind("warning")
            self.scan_banner.set_text(
                f"About {ratio * 100:.0f}% of the sampled pages already contain real "
                f"text. OCR is only needed for the scanned pages — keep "
                f"“Skip pages that already contain text” on.")
        self.scan_banner.setVisible(True)

    # -- run ---------------------------------------------------------------
    def validate(self) -> str:
        if not deps.has_tesseract():
            return "The OCR engine is not available on this computer."
        if not self.selected_languages():
            return "Select at least one OCR language."
        if self.image_list.count() == 0 and not self.source:
            return "Choose a scanned PDF or add some images."
        if self.source and not self.pages.pages():
            return "That page selection matches no pages."
        return ""

    def _options(self) -> ocr.OcrOptions:
        return ocr.OcrOptions(
            languages=self.selected_languages(),
            dpi=int(self.dpi.currentData()),
            mode=self.mode.value(),
            pages=self.pages.text(),
            grayscale=self.grayscale.isChecked(),
            deskew=self.deskew.isChecked(),
            auto_rotate=self.autorotate.isChecked(),
            denoise=self.denoise.isChecked(),
            contrast=self.contrast.isChecked(),
            binarize=self.binarize.isChecked(),
            psm=int(self.psm.currentData()),
            text_layer=self.engine.currentData(),
            skip_pages_with_text=self.skip_text.isChecked(),
        )

    def start(self) -> None:
        opts = self._options()
        self._remember(opts)

        if self.image_list.count():
            files = [Path(p) for p in self.image_list.paths()]
            target = (self.output.resolved(files[0], "ocr")
                      if opts.mode != "text" else None)
            self._source_for_history = files[0]
            self.run_job(ocr.ocr_images, files, target, opts,
                         make_pdf=(opts.mode != "text"),
                         name=f"OCR on {len(files)} image(s)")
        else:
            target = self.output.resolved(self.source, "ocr") if opts.mode != "text" else None
            self._source_for_history = self.source
            self.run_job(ocr.ocr_pdf, self.source, target, opts, self.password,
                         name=f"OCR on {self.source.name}")

    def _remember(self, opts: ocr.OcrOptions) -> None:
        config.set("ocr_languages", opts.languages)
        config.set("ocr_dpi", opts.dpi)
        config.set("ocr_mode", opts.mode)
        config.set("ocr_deskew", opts.deskew)
        config.set("ocr_rotate", opts.auto_rotate)
        config.set("ocr_grayscale", opts.grayscale)
        config.set("ocr_denoise", opts.denoise)
        config.set("ocr_contrast", opts.contrast)
        config.set("ocr_binarize", opts.binarize)
        config.set("ocr_psm", opts.psm)
        config.set("ocr_skip_text_pages", opts.skip_pages_with_text)

    def on_success(self, result: ocr.OcrResult) -> None:
        self._result = result
        detail = []
        if result.mean_confidence:
            detail.append(f"Average confidence {result.mean_confidence:.0f}%")
        if result.engine == "rebuild":
            detail.append("Pages were rebuilt from the OCR render to support the "
                          "selected script.")
        elif result.engine == "native":
            detail.append("Your original page images are unchanged — the text sits "
                          "invisibly on top.")

        self.show_result(result.message, result.outputs, "\n".join(detail),
                         result.warnings)

        if result.text.strip():
            self.text_view.setPlainText(result.text)
            self.text_card.setVisible(True)

        src = getattr(self, "_source_for_history", self.source)
        self.record("OCR", src, result.output, result.ocred_pages,
                    "+".join(self.selected_languages()))
        db.bump_stat("ocr_documents")
        db.bump_stat("ocr_pages", result.ocred_pages)
        db.bump_stat("pdfs_processed")
        if result.outputs:
            self.finish_output(result.outputs, self.output)
        self.toast(f"OCR complete — {result.ocred_pages} page(s) recognised")

    # -- text export -------------------------------------------------------
    def _copy_text(self) -> None:
        text = self.text_view.toPlainText()
        if not text.strip():
            self.toast("There is no text to copy", "warning")
            return
        C.copy_to_clipboard(text)
        self.toast(f"Copied {len(text.split()):,} words")

    def _export(self, fmt: str) -> None:
        from PySide6.QtWidgets import QFileDialog
        from app.core.jobs import NullContext
        from app.core.textops import TextResult, export_text

        text = self.text_view.toPlainText()
        if not text.strip():
            self.toast("There is no text to save", "warning")
            return

        src = getattr(self, "_source_for_history", self.source)
        default = str((src.parent if src else Path.home()) /
                      f"{(src.stem if src else 'ocr')}_ocr.{fmt}")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save recognised text", default, f"{fmt.upper()} (*.{fmt})")
        if not path:
            return

        result = TextResult(text=text,
                            pages=[p.text for p in (self._result.pages if self._result else [])]
                            or [text])
        try:
            out = export_text(NullContext(), result, path, fmt,
                              src.name if src else "")
        except Exception as e:
            self.report_failure(str(e))
            return
        self.toast(f"Saved {out.name}")
        utils.reveal_in_explorer(out)
