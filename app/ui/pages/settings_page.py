"""Settings — preferences, external tool paths and diagnostics."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from app import branding, config, db, paths
from app.core import deps, utils
from app.ui import components as C
from app.ui.pages.base import BasePage


class SettingsPage(BasePage):
    TITLE = "Settings"
    SUBTITLE = "Preferences, OCR paths and diagnostics"

    def build(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._appearance_tab(), "Appearance")
        tabs.addTab(self._files_tab(), "Files and output")
        tabs.addTab(self._ocr_tab(), "OCR")
        tabs.addTab(self._performance_tab(), "Performance")
        tabs.addTab(self._privacy_tab(), "Privacy")
        tabs.addTab(self._diagnostics_tab(), "Diagnostics")
        self.scroll.add(tabs)

        row = QHBoxLayout()
        reset = QPushButton("Restore all defaults")
        reset.setObjectName("DangerGhost")
        reset.clicked.connect(self._reset)
        export = QPushButton("Export settings")
        export.setObjectName("Ghost")
        export.clicked.connect(self._export)
        imp = QPushButton("Import settings")
        imp.setObjectName("Ghost")
        imp.clicked.connect(self._import)
        row.addWidget(reset)
        row.addWidget(export)
        row.addWidget(imp)
        row.addStretch(1)
        self.scroll.add_layout(row)

    # ------------------------------------------------------------ appearance
    def _appearance_tab(self) -> QWidget:
        page = C.ScrollArea(margins=(6, 14, 6, 14))

        card = C.Card("Theme")
        self.theme = C.SegmentedControl(
            [("Light", "Light"), ("Dark", "Dark"), ("System", "Match Windows")],
            config.get("theme", "Light"))
        self.theme.changed.connect(self._on_theme)
        card.add(C.FormRow("Appearance", self.theme))

        self.scale = C.combo([
            (0.9, "90% — more on screen"),
            (1.0, "100% — default"),
            (1.1, "110%"),
            (1.25, "125% — easier to read"),
            (1.5, "150% — large"),
        ], config.get_float("ui_scale", 1.0))
        self.scale.currentIndexChanged.connect(self._on_scale)
        card.add(C.FormRow("Interface size", self.scale,
                           "Applies immediately to most of the interface; restart for "
                           "a completely consistent result."))
        page.add(card)

        window_card = C.Card("Window")
        self.remember = C.check("Remember the window size and position",
                                config.get_bool("remember_window", True))
        self.remember.toggled.connect(lambda v: config.set("remember_window", v))
        window_card.add(self.remember)
        self.collapsed = C.check("Start with the sidebar collapsed",
                                 config.get_bool("sidebar_collapsed", False))
        self.collapsed.toggled.connect(self._on_collapse)
        window_card.add(self.collapsed)
        page.add(window_card)

        page.add_stretch()
        return page

    def _on_theme(self, value: str) -> None:
        if self.window_ref:
            resolved = value
            if value == "System":
                from app.main import resolve_theme_name
                config.set("theme", "System")
                resolved = resolve_theme_name()
                self.window_ref.apply_theme(resolved)
                config.set("theme", "System")
                return
            self.window_ref.apply_theme(value)

    def _on_scale(self) -> None:
        value = float(self.scale.currentData())
        config.set("ui_scale", value)
        if self.window_ref:
            self.window_ref.apply_theme(config.get("theme", "Light"))
        self.toast("Interface size updated")

    def _on_collapse(self, value: bool) -> None:
        config.set("sidebar_collapsed", value)
        if self.window_ref:
            self.window_ref.sidebar.set_collapsed(value)

    # ----------------------------------------------------------------- files
    def _files_tab(self) -> QWidget:
        page = C.ScrollArea(margins=(6, 14, 6, 14))

        card = C.Card("Where results are saved")
        self.out_mode = C.SegmentedControl([
            ("same_folder", "Beside the original"),
            ("fixed", "One folder"),
            ("ask", "Ask each time"),
        ], config.get("output_mode", "same_folder"))
        self.out_mode.changed.connect(lambda v: config.set("output_mode", v))
        card.add(C.FormRow("Default location", self.out_mode))

        self.out_dir = C.PathPicker("folder", default=str(config.get("output_dir", "")))
        self.out_dir.changed.connect(lambda v: config.set("output_dir", v))
        card.add(C.FormRow("Default folder", self.out_dir))

        self.suffix_style = C.SegmentedControl([
            ("descriptive", "document_ocr.pdf"),
            ("none", "document.pdf"),
        ], config.get("output_suffix_style", "descriptive"))
        self.suffix_style.changed.connect(
            lambda v: config.set("output_suffix_style", v))
        card.add(C.FormRow("File naming", self.suffix_style,
                           "Descriptive names make it obvious which tool produced "
                           "which file."))
        page.add(card)

        safety = C.Card("File safety")
        self.overwrite = C.SegmentedControl([
            ("unique", "Add a number (document_ocr_2.pdf)"),
            ("ask", "Ask what to do"),
            ("overwrite", "Overwrite"),
        ], config.get("overwrite_policy", "unique"))
        self.overwrite.changed.connect(lambda v: config.set("overwrite_policy", v))
        safety.add(C.FormRow("If the output name already exists", self.overwrite))

        safety.add(C.Banner(
            "An original file is never replaced unless you explicitly ask for it. "
            "The default naming scheme always produces a new file.", "success"))

        self.open_after = C.check("Open results when they are finished",
                                  config.get_bool("open_output_after", True))
        self.open_after.toggled.connect(lambda v: config.set("open_output_after", v))
        safety.add(self.open_after)

        self.backup_redact = C.check(
            "Always back up before redacting",
            config.get_bool("backup_before_redact", True))
        self.backup_redact.toggled.connect(
            lambda v: config.set("backup_before_redact", v))
        safety.add(self.backup_redact)

        self.warn_large = C.spin(config.get_int("warn_large_files_mb", 200), 0, 5000, " MB")
        self.warn_large.valueChanged.connect(
            lambda v: config.set("warn_large_files_mb", v))
        safety.add(C.FormRow("Warn about files larger than", self.warn_large,
                             "0 disables the warning."))
        page.add(safety)

        page.add_stretch()
        return page

    # ------------------------------------------------------------------- OCR
    def _ocr_tab(self) -> QWidget:
        page = C.ScrollArea(margins=(6, 14, 6, 14))

        status = C.Card("OCR engine")
        found = deps.tesseract_path()
        if found:
            status.add(C.Banner(
                f"Tesseract found:\n{found}\n{deps.tesseract_version()}", "success"))
        else:
            status.add(C.Banner(
                "Tesseract OCR was not found, so recognition is unavailable. "
                "Everything else in the application works normally.", "warning"))
            get_row = QHBoxLayout()
            get = QPushButton("Download Tesseract OCR")
            get.setObjectName("Primary")
            get.clicked.connect(self._download_tesseract)
            get_row.addWidget(get)
            hint = QLabel("Install it, then press “Apply and re-check” below.")
            hint.setObjectName("Subtle")
            get_row.addWidget(hint, 1)
            status.add_layout(get_row)

        self.tess_path = C.PathPicker(
            "open", "Programs (*.exe);;All files (*.*)",
            "Leave blank to detect automatically",
            default=str(config.get("tesseract_path", "")))
        status.add(C.FormRow("Tesseract location", self.tess_path))

        row = QHBoxLayout()
        apply_btn = QPushButton("Apply and re-check")
        apply_btn.setObjectName("Primary")
        apply_btn.clicked.connect(self._apply_tesseract)
        row.addWidget(apply_btn)
        row.addStretch(1)
        status.add_layout(row)
        page.add(status)

        langs = C.Card("Installed language packs")
        installed = deps.installed_languages()
        if installed:
            names = ", ".join(f"{deps.language_label(c)} ({c})" for c in installed)
            lbl = QLabel(names)
            lbl.setWordWrap(True)
            langs.add(lbl)
        else:
            langs.add(QLabel("No language packs were detected."))

        tessdata = ""
        if found:
            from app.core.deps import _tessdata_for
            tessdata = _tessdata_for(found) or ""
        langs.add(C.muted(
            f"Language files live in:\n{tessdata or 'the tessdata folder next to Tesseract'}\n\n"
            f"To add a language, download its .traineddata file from the Tesseract "
            f"project and copy it into that folder, then press “Apply and re-check”."))
        page.add(langs)

        defaults = C.Card("Default OCR settings",
                          "Used when a new OCR job starts. You can change them per job.")
        self.ocr_dpi = C.combo([(d, f"{d} DPI") for d in (150, 200, 300, 400, 600)],
                               config.get_int("ocr_dpi", 300))
        self.ocr_dpi.currentIndexChanged.connect(
            lambda: config.set("ocr_dpi", int(self.ocr_dpi.currentData())))
        defaults.add(C.FormRow("Resolution", self.ocr_dpi))

        for key, label in [("ocr_deskew", "Straighten crooked scans"),
                           ("ocr_rotate", "Correct page orientation"),
                           ("ocr_grayscale", "Convert to grayscale"),
                           ("ocr_denoise", "Reduce speckle noise"),
                           ("ocr_contrast", "Enhance contrast"),
                           ("ocr_binarize", "Convert to black and white"),
                           ("ocr_skip_text_pages", "Skip pages that already have text")]:
            cb = C.check(label, config.get_bool(key, False))
            cb.toggled.connect(lambda v, k=key: config.set(k, v))
            defaults.add(cb)
        page.add(defaults)

        page.add_stretch()
        return page

    def _download_tesseract(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(deps.TESSERACT_DOWNLOAD_URL))
        self.toast("Opened the Tesseract download page in your browser")

    def _apply_tesseract(self) -> None:
        config.set("tesseract_path", self.tess_path.text())
        deps.refresh()
        found = deps.tesseract_path()
        if found:
            langs = deps.installed_languages()
            self.toast(f"OCR engine found — {len(langs)} language pack(s) available")
        else:
            self.toast("Still not found — install Tesseract or browse to "
                       "tesseract.exe", "warning")

    # ----------------------------------------------------------- performance
    def _performance_tab(self) -> QWidget:
        page = C.ScrollArea(margins=(6, 14, 6, 14))

        card = C.Card("Processing")
        cpu = os.cpu_count() or 2
        self.threads = C.combo(
            [(0, f"Automatic ({max(1, cpu - 1)} of {cpu} cores)")] +
            [(n, f"{n} core{'s' if n > 1 else ''}") for n in range(1, cpu + 1)],
            config.get_int("worker_threads", 0))
        self.threads.currentIndexChanged.connect(
            lambda: config.set("worker_threads", int(self.threads.currentData())))
        card.add(C.FormRow("Parallel tasks", self.threads,
                           "Takes effect for jobs started after a restart."))

        self.render_dpi = C.combo([(d, f"{d} DPI") for d in (96, 120, 150, 200, 300)],
                                  config.get_int("render_dpi", 150))
        self.render_dpi.currentIndexChanged.connect(
            lambda: config.set("render_dpi", int(self.render_dpi.currentData())))
        card.add(C.FormRow("Viewer render quality", self.render_dpi,
                           "Higher looks sharper when zoomed in and uses more memory."))
        page.add(card)

        cache = C.Card("Storage")
        data_dir = paths.user_data_dir()
        size = _folder_size(data_dir)
        self.storage_label = QLabel(
            f"Application data folder:\n{data_dir}\n\nCurrently using "
            f"{utils.human_size(size)}.")
        self.storage_label.setWordWrap(True)
        cache.add(self.storage_label)

        row = QHBoxLayout()
        open_btn = QPushButton("Open the folder")
        open_btn.setObjectName("Ghost")
        open_btn.clicked.connect(lambda: utils.open_path(data_dir))
        clear_btn = QPushButton("Clear temporary files")
        clear_btn.setObjectName("Ghost")
        clear_btn.clicked.connect(self._clear_temp)
        row.addWidget(open_btn)
        row.addWidget(clear_btn)
        row.addStretch(1)
        cache.add_layout(row)
        page.add(cache)

        page.add_stretch()
        return page

    def _clear_temp(self) -> None:
        import shutil
        removed = 0
        for folder in (paths.temp_dir(), paths.cache_dir()):
            for child in folder.glob("*"):
                try:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink()
                    removed += 1
                except OSError:
                    continue
        self.storage_label.setText(
            f"Application data folder:\n{paths.user_data_dir()}\n\nCurrently using "
            f"{utils.human_size(_folder_size(paths.user_data_dir()))}.")
        self.toast(f"Removed {removed} temporary item(s)")

    # --------------------------------------------------------------- privacy
    def _privacy_tab(self) -> QWidget:
        page = C.ScrollArea(margins=(6, 14, 6, 14))

        card = C.Card("Local processing")
        card.add(C.Banner(branding.PRIVACY_STATEMENT, "success"))
        detail = QLabel(branding.PRIVACY_DETAIL)
        detail.setWordWrap(True)
        card.add(detail)
        page.add(card)

        history = C.Card("History")
        self.hist_enabled = C.check("Keep a history of processed files",
                                    config.get_bool("history_enabled", True))
        self.hist_enabled.toggled.connect(lambda v: config.set("history_enabled", v))
        history.add(self.hist_enabled)

        self.store_paths = C.check(
            "Record full file paths, not just names",
            config.get_bool("store_paths_in_history", True),
            "Turn off if the folder names themselves are sensitive.")
        self.store_paths.toggled.connect(
            lambda v: config.set("store_paths_in_history", v))
        history.add(self.store_paths)

        self.retention = C.combo([
            (0, "Keep forever"),
            (7, "Delete after 7 days"),
            (30, "Delete after 30 days"),
            (90, "Delete after 90 days"),
            (180, "Delete after 180 days"),
            (365, "Delete after a year"),
        ], config.get_int("history_retention_days", 180))
        self.retention.currentIndexChanged.connect(
            lambda: config.set("history_retention_days",
                               int(self.retention.currentData())))
        history.add(C.FormRow("Automatic clean-up", self.retention))

        row = QHBoxLayout()
        clear = QPushButton("Delete all history now")
        clear.setObjectName("DangerGhost")
        clear.clicked.connect(self._clear_history)
        row.addWidget(clear)
        row.addStretch(1)
        history.add_layout(row)
        page.add(history)

        net = C.Card("Network")
        self.updates = C.check(
            "Check for updates when the application starts",
            config.get_bool("check_updates", False),
            "The only feature that contacts the internet. It is off by default.")
        self.updates.toggled.connect(lambda v: config.set("check_updates", v))
        net.add(self.updates)
        page.add(net)

        page.add_stretch()
        return page

    def _clear_history(self) -> None:
        db.clear_history()
        self.toast("History deleted")

    # ----------------------------------------------------------- diagnostics
    def _diagnostics_tab(self) -> QWidget:
        page = C.ScrollArea(margins=(6, 14, 6, 14))

        caps = C.Card("Available features")
        for name, ok in deps.capabilities().items():
            row = QHBoxLayout()
            label = QLabel(name.replace("_", " ").capitalize())
            row.addWidget(label, 1)
            row.addWidget(C.Badge("Available" if ok else "Unavailable",
                                  "success" if ok else ""))
            holder = QWidget()
            holder.setLayout(row)
            caps.add(holder)

        missing = deps.missing_optional()
        if missing:
            for feature, remedy in missing:
                caps.add(C.Banner(f"{feature}: {remedy}", "warning"))
        page.add(caps)

        report_card = C.Card(
            "System report",
            f"Include this when contacting support at {branding.SUPPORT_EMAIL}.")
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setObjectName("Mono")
        self.report.setMinimumHeight(320)
        self.report.setPlainText(deps.report())
        report_card.add(self.report)

        row = QHBoxLayout()
        copy = QPushButton("Copy to clipboard")
        copy.setObjectName("Primary")
        copy.clicked.connect(self._copy_report)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self._refresh_report)
        save = QPushButton("Save to a file")
        save.setObjectName("Ghost")
        save.clicked.connect(self._save_report)
        logs = QPushButton("Open the log folder")
        logs.setObjectName("Ghost")
        logs.clicked.connect(lambda: utils.open_path(paths.logs_dir()))
        for b in (copy, refresh, save, logs):
            row.addWidget(b)
        row.addStretch(1)
        report_card.add_layout(row)
        page.add(report_card)

        page.add_stretch()
        return page

    def _copy_report(self) -> None:
        C.copy_to_clipboard(self.report.toPlainText())
        self.toast("Report copied to the clipboard")

    def _refresh_report(self) -> None:
        deps.refresh()
        self.report.setPlainText(deps.report())
        self.toast("Report refreshed")

    def _save_report(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the system report",
            str(Path.home() / "pdf_master_diagnostics.txt"), "Text (*.txt)")
        if path:
            Path(path).write_text(self.report.toPlainText(), encoding="utf-8")
            self.toast("Report saved")
            utils.reveal_in_explorer(path)

    # ---------------------------------------------------------------- global
    def _reset(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Restore defaults?")
        box.setText("Every preference will go back to its original value.")
        box.setInformativeText("Your files and history are not affected.")
        yes = box.addButton("Restore defaults", QMessageBox.AcceptRole)
        yes.setObjectName("DangerButton")
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        config.reset_to_defaults()
        if self.window_ref:
            self.window_ref.apply_theme(config.get("theme", "Light"))
        self.toast("Defaults restored — reopen Settings to see them")

    def _export(self) -> None:
        import json
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Export settings",
            str(Path.home() / "pdf_master_settings.json"), "JSON (*.json)")
        if not path:
            return
        Path(path).write_text(json.dumps(config.export_settings(), indent=2),
                              encoding="utf-8")
        self.toast("Settings exported")

    def _import(self) -> None:
        import json
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Import settings", str(Path.home()), "JSON (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            n = config.import_settings(data)
        except Exception as e:
            self.toast(f"Could not import: {e}", "error")
            return
        if self.window_ref:
            self.window_ref.apply_theme(config.get("theme", "Light"))
        self.toast(f"Imported {n} setting(s) — reopen Settings to see them")


def _folder_size(folder: Path) -> int:
    total = 0
    try:
        for p in folder.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total
