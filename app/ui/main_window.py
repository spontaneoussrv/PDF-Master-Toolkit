"""Application shell: sidebar, header, page router, status bar."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QByteArray, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QStatusBar, QVBoxLayout, QWidget)

from app import branding, config, db, paths, theme
from app.core import deps, utils
from app.core.jobs import JobManager
from app.ui import components as C
from app.ui.pages import BY_KEY, MODULES, ModuleSpec, get
from app.ui.sidebar import Sidebar

DROPPABLE = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp",
             ".docx", ".xlsx", ".xls", ".csv")


class MainWindow(QMainWindow):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(branding.window_title())
        self.setMinimumSize(1120, 720)
        self.setAcceptDrops(True)

        self.jobs = JobManager(config.worker_count(), self)
        self.jobs.active_changed.connect(self._on_jobs_changed)

        self._pages: dict[str, QWidget] = {}
        self._history: list[str] = []
        self._current_key = ""

        central = QWidget()
        central.setObjectName("AppCanvas")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.navigate.connect(self.navigate)
        root.addWidget(self.sidebar)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)
        right_lay.addWidget(self._build_header())

        self.stack = QStackedWidget()
        right_lay.addWidget(self.stack, 1)
        root.addWidget(right, 1)

        self.setCentralWidget(central)
        self.setStatusBar(self._build_statusbar())

        self._install_shortcuts()
        self._restore_window_state()

        if config.get_bool("sidebar_collapsed", False):
            self.sidebar.set_collapsed(True)

        self.navigate("dashboard")
        QTimer.singleShot(900, self._startup_checks)

    # ------------------------------------------------------------------ UI
    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("HeaderBar")
        bar.setFixedHeight(66)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(24, 10, 18, 10)
        lay.setSpacing(12)

        col = QVBoxLayout()
        col.setSpacing(1)
        self.page_title = QLabel("Dashboard")
        self.page_title.setObjectName("PageTitle")
        self.page_subtitle = QLabel("")
        self.page_subtitle.setObjectName("PageSubtitle")
        col.addWidget(self.page_title)
        col.addWidget(self.page_subtitle)
        lay.addLayout(col, 1)

        self.back_btn = QPushButton("←")
        self.back_btn.setObjectName("HeaderIconButton")
        self.back_btn.setToolTip("Back  (Alt+Left)")
        self.back_btn.clicked.connect(self.go_back)
        self.back_btn.setEnabled(False)
        lay.addWidget(self.back_btn)

        self.open_btn = QPushButton("Open PDF")
        self.open_btn.setObjectName("Primary")
        self.open_btn.setToolTip("Open a document in the viewer  (Ctrl+O)")
        self.open_btn.clicked.connect(self.open_document)
        lay.addWidget(self.open_btn)

        self.recent_btn = QPushButton("🕘")
        self.recent_btn.setObjectName("HeaderIconButton")
        self.recent_btn.setToolTip("Recent files  (Ctrl+R)")
        self.recent_btn.clicked.connect(self.show_recent_files)
        lay.addWidget(self.recent_btn)

        self.help_btn = QPushButton("?")
        self.help_btn.setObjectName("HeaderIconButton")
        self.help_btn.setToolTip("Keyboard shortcuts  (F1)")
        self.help_btn.clicked.connect(self.show_shortcuts)
        lay.addWidget(self.help_btn)

        self.theme_btn = QPushButton("☾")
        self.theme_btn.setObjectName("HeaderIconButton")
        self.theme_btn.setToolTip("Switch between light and dark  (Ctrl+D)")
        self.theme_btn.clicked.connect(self.toggle_theme)
        lay.addWidget(self.theme_btn)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("HeaderIconButton")
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(lambda: self.navigate("settings"))
        lay.addWidget(self.settings_btn)

        self._sync_theme_button()
        return bar

    def _build_statusbar(self) -> QStatusBar:
        sb = QStatusBar()
        sb.setSizeGripEnabled(True)

        self.status_label = QLabel("Ready")
        sb.addWidget(self.status_label, 1)

        self.jobs_label = QLabel("")
        sb.addPermanentWidget(self.jobs_label)

        self.privacy_label = QLabel("🔒  Processed locally on this computer")
        self.privacy_label.setToolTip(branding.PRIVACY_STATEMENT)
        sb.addPermanentWidget(self.privacy_label)
        return sb

    def _install_shortcuts(self) -> None:
        def sc(seq: str, slot):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(slot)
            return s

        sc("Ctrl+K", self._focus_search)
        sc("Ctrl+F", self._focus_search)
        sc("Ctrl+B", self.sidebar.toggle)
        sc("Ctrl+O", self.open_document)
        sc("Ctrl+D", self.toggle_theme)
        sc("Alt+Left", self.go_back)
        sc("Ctrl+1", lambda: self.navigate("dashboard"))
        sc("Ctrl+2", lambda: self.navigate("merge"))
        sc("Ctrl+3", lambda: self.navigate("ocr"))
        sc("Ctrl+4", lambda: self.navigate("compress"))
        sc("Ctrl+,", lambda: self.navigate("settings"))
        sc("Ctrl+R", self.show_recent_files)
        sc("F1", self.show_shortcuts)
        sc("Shift+F1", lambda: self.navigate("about"))
        sc("Escape", self._on_escape)

    def _focus_search(self) -> None:
        self.sidebar.focus_search()
        self.sidebar.search_box.returnPressed.connect(self._goto_first_result,
                                                      Qt.UniqueConnection)

    def _goto_first_result(self) -> None:
        key = self.sidebar.first_search_result()
        if key:
            self.navigate(key)
            self.sidebar.search_box.clear()

    def _on_escape(self) -> None:
        if self.sidebar.search_box.hasFocus():
            self.sidebar.search_box.clear()
            self.sidebar.search_box.clearFocus()

    # ------------------------------------------------------------- routing
    def navigate(self, key: str, payload=None) -> None:
        spec = get(key)
        if spec is None:
            return

        page = self._pages.get(key)
        if page is None:
            page = self._create_page(spec)
            if page is None:
                return

        if self._current_key and self._current_key != key:
            current = self._pages.get(self._current_key)
            if current is not None and hasattr(current, "on_hide"):
                try:
                    current.on_hide()
                except Exception:
                    pass
            self._history.append(self._current_key)
            self._history = self._history[-30:]

        self._current_key = key
        self.stack.setCurrentWidget(page)
        self.sidebar.set_current(key)
        self.page_title.setText(spec.title)
        self.page_subtitle.setText(spec.subtitle)
        self.setWindowTitle(branding.window_title(spec.title))
        self.back_btn.setEnabled(bool(self._history))

        if hasattr(page, "ensure_built"):
            page.ensure_built()
        if payload is not None and hasattr(page, "receive"):
            try:
                page.receive(payload)
            except Exception as e:
                self.set_status(f"Could not open that item: {e}")
        if hasattr(page, "on_show"):
            try:
                page.on_show()
            except Exception:
                pass

    def _create_page(self, spec: ModuleSpec) -> QWidget | None:
        try:
            cls = spec.load()
            page = cls(window=self)
        except Exception as e:
            import traceback
            traceback.print_exc()
            placeholder = QWidget()
            lay = QVBoxLayout(placeholder)
            lay.setContentsMargins(28, 24, 28, 24)
            lay.addWidget(C.Banner(
                f"“{spec.title}” could not be loaded.\n\n{e}\n\n"
                f"The rest of the application is unaffected. Please report this "
                f"to {branding.SUPPORT_EMAIL} with the diagnostics from Settings.",
                "danger"))
            lay.addStretch(1)
            self._pages[spec.key] = placeholder
            self.stack.addWidget(placeholder)
            return placeholder

        if hasattr(page, "navigate"):
            page.navigate.connect(self.navigate)
        if hasattr(page, "status_message"):
            page.status_message.connect(self.set_status)

        self._pages[spec.key] = page
        self.stack.addWidget(page)
        return page

    def go_back(self) -> None:
        if not self._history:
            return
        key = self._history.pop()
        target = self._current_key
        self._current_key = ""          # avoid re-pushing onto history
        self.navigate(key)
        self._history = [h for h in self._history if h != target] or self._history
        self.back_btn.setEnabled(bool(self._history))

    def current_page(self) -> QWidget | None:
        return self._pages.get(self._current_key)

    # ------------------------------------------------------------- actions
    def open_document(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", str(config.get("last_browse_dir", "") or Path.home()),
            "PDF files (*.pdf);;All files (*.*)")
        if path:
            config.set("last_browse_dir", str(Path(path).parent))
            self.navigate("viewer", path)

    def show_shortcuts(self) -> None:
        from app.ui.dialogs import ShortcutsDialog
        ShortcutsDialog(self).exec()

    def show_recent_files(self) -> None:
        from app.ui.dialogs import RecentFilesDialog
        dlg = RecentFilesDialog(self)
        dlg.file_chosen.connect(self._open_recent)
        dlg.exec()

    def _open_recent(self, path: str) -> None:
        p = Path(path)
        if not p.is_file():
            self.set_status(f"{p.name} no longer exists")
            return
        if p.suffix.lower() == ".pdf":
            self.navigate("viewer", str(p))
        else:
            page = self.current_page()
            if page is not None and hasattr(page, "handle_files"):
                page.handle_files([str(p)])

    def show_welcome(self) -> None:
        from app.ui.dialogs import WelcomeDialog
        dlg = WelcomeDialog(self)
        dlg.navigate.connect(self.navigate)
        dlg.exec()

    def toggle_theme(self) -> None:
        current = config.get("theme", "Light")
        new = "Dark" if current != "Dark" else "Light"
        self.apply_theme(new)

    def apply_theme(self, name: str) -> None:
        config.set("theme", name)
        palette = theme.resolve(name)
        appl = QApplication.instance()
        if appl is not None:
            appl.setPalette(theme.qt_palette(palette))
            appl.setStyleSheet(theme.build_stylesheet(
                palette, config.get_float("ui_scale", 1.0)))
        self._sync_theme_button()

    def _sync_theme_button(self) -> None:
        dark = config.get("theme", "Light") == "Dark"
        self.theme_btn.setText("☀" if dark else "☾")
        self.theme_btn.setToolTip("Switch to the light theme" if dark
                                  else "Switch to the dark theme")

    def set_status(self, message: str) -> None:
        self.status_label.setText(message or "Ready")

    def _on_jobs_changed(self, count: int) -> None:
        if count == 0:
            self.jobs_label.setText("")
        elif count == 1:
            self.jobs_label.setText("⏳  1 task running")
        else:
            self.jobs_label.setText(f"⏳  {count} tasks running")

    # ---------------------------------------------------------- drag & drop
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                p = Path(u.toLocalFile())
                if p.is_dir() or p.suffix.lower() in DROPPABLE:
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event) -> None:
        paths: list[str] = []
        for u in event.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_dir():
                paths += [str(c) for c in sorted(p.rglob("*"))
                          if c.suffix.lower() in DROPPABLE]
            elif p.suffix.lower() in DROPPABLE:
                paths.append(str(p))
        if not paths:
            return
        event.acceptProposedAction()

        page = self.current_page()
        if page is not None and hasattr(page, "handle_files"):
            try:
                page.handle_files(paths)
                self.set_status(f"Added {len(paths)} file(s)")
                return
            except Exception:
                pass
        self.navigate("dashboard")
        page = self.current_page()
        if page is not None and hasattr(page, "handle_files"):
            page.handle_files(paths)

    # ------------------------------------------------------------ lifecycle
    def _startup_checks(self) -> None:
        missing = deps.missing_optional()
        critical = [m for m in missing if m[0] == "OCR"]
        if critical and not config.get_bool("ocr_warning_shown", False):
            C.toast(self, "OCR is unavailable — see Settings → OCR for details.",
                    "warning", 5200)
            config.set("ocr_warning_shown", True)

        if not config.get_bool("first_run_complete", False):
            self.show_welcome()

        retention = config.get_int("history_retention_days", 180)
        if retention > 0:
            removed = db.prune_history(retention)
            if removed:
                self.set_status(f"Cleared {removed} history entries older than "
                                f"{retention} days")

    def _restore_window_state(self) -> None:
        if not config.get_bool("remember_window", True):
            self._center_default()
            return
        raw = config.get("window_geometry", "")
        if raw:
            try:
                self.restoreGeometry(QByteArray.fromBase64(raw.encode("ascii")))
                return
            except Exception:
                pass
        self._center_default()

    def _center_default(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1320, 860)
            return
        available = screen.availableGeometry()
        w = min(1360, int(available.width() * 0.86))
        h = min(900, int(available.height() * 0.88))
        self.resize(w, h)
        self.move(available.center().x() - w // 2, available.center().y() - h // 2)

    def closeEvent(self, event) -> None:
        if self.jobs.is_busy:
            names = self.jobs.active_names()
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Work in progress")
            box.setText(f"{len(names)} task(s) are still running.")
            box.setInformativeText(
                "Closing now will cancel them. Files already written are kept; "
                "anything in progress will be discarded.\n\n" + "\n".join(
                    f"• {n}" for n in names[:6]))
            stop = box.addButton("Cancel and close", QMessageBox.AcceptRole)
            box.addButton("Keep working", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not stop:
                event.ignore()
                return

        for page in self._pages.values():
            if hasattr(page, "can_close") and not page.can_close():
                event.ignore()
                return

        if config.get_bool("remember_window", True):
            try:
                config.set("window_geometry",
                           bytes(self.saveGeometry().toBase64()).decode("ascii"))
            except Exception:
                pass

        self.jobs.shutdown()
        for page in self._pages.values():
            for attr in ("viewer", "organizer", "crop_canvas", "placer"):
                w = getattr(page, attr, None)
                if w is not None and hasattr(w, "close_document"):
                    try:
                        w.close_document()
                    except Exception:
                        pass
        db.close()
        super().closeEvent(event)
