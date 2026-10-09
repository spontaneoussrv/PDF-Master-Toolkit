"""Application entry point."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt, QTimer
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

from app import branding, config, db, paths, theme


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------

def setup_logging() -> Path:
    log_file = paths.logs_dir() / f"{datetime.now():%Y-%m-%d}.log"
    handlers: list[logging.Handler] = [logging.FileHandler(log_file, encoding="utf-8")]
    if not paths.is_frozen():
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        handlers=handlers,
        force=True,
    )
    for noisy in ("pdf2docx", "pdfminer", "PIL", "fontTools", "camelot", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    # keep the last 30 log files
    try:
        logs = sorted(paths.logs_dir().glob("*.log"))
        for old in logs[:-30]:
            old.unlink(missing_ok=True)
    except OSError:
        pass
    return log_file


LOG = logging.getLogger("pdfmaster")


# ---------------------------------------------------------------------------
# crash handling
# ---------------------------------------------------------------------------

def install_excepthook(app: QApplication) -> None:
    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        LOG.error("Unhandled exception:\n%s", text)
        try:
            box = QMessageBox()
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle(f"{branding.APP_NAME} — unexpected error")
            box.setText("Something went wrong.")
            box.setInformativeText(
                f"{exc_type.__name__}: {exc_value}\n\n"
                f"The application is still running and your files have not been "
                f"changed. If this keeps happening, send the details below to "
                f"{branding.SUPPORT_EMAIL}."
            )
            box.setDetailedText(text)
            box.exec()
        except Exception:
            pass

    sys.excepthook = hook


# ---------------------------------------------------------------------------
# platform integration
# ---------------------------------------------------------------------------

def configure_windows() -> None:
    """Taskbar identity and per-monitor DPI awareness."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            branding.APP_USER_MODEL_ID)
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)     # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def app_icon() -> QIcon:
    for name in (branding.ICON_FILE, branding.LOGO_MARK_FILE, branding.LOGO_FILE):
        p = paths.asset(name)
        if p.is_file():
            icon = QIcon(str(p))
            if not icon.isNull():
                return icon
    return _generated_icon()


def _generated_icon() -> QIcon:
    """Fallback monogram tile so the app always has a taskbar icon."""
    from PySide6.QtGui import QColor, QFont as QF, QLinearGradient, QPainter, QBrush

    sizes = (16, 24, 32, 48, 64, 128, 256)
    icon = QIcon()
    for size in sizes:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0.0, QColor(branding.BRAND_ACCENT))
        grad.setColorAt(1.0, QColor(branding.BRAND_ACCENT_ALT))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        radius = size * 0.22
        p.drawRoundedRect(0, 0, size, size, radius, radius)
        if size >= 24:
            f = QF("Segoe UI", int(size * 0.40))
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(pm.rect(), Qt.AlignCenter, branding.LOGO_MONOGRAM)
        p.end()
        icon.addPixmap(pm)
    return icon


def base_font() -> QFont:
    for family in ("Segoe UI Variable Display", "Segoe UI", "Inter", "Noto Sans"):
        f = QFont(family, 10)
        if f.exactMatch() or family in ("Segoe UI", "Noto Sans"):
            f.setStyleStrategy(QFont.PreferAntialias)
            return f
    return QFont()


# ---------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------

def resolve_theme_name() -> str:
    name = config.get("theme", "Light")
    if name != "System":
        return name
    try:
        from PySide6.QtGui import QGuiApplication
        scheme = QGuiApplication.styleHints().colorScheme()
        return "Dark" if scheme == Qt.ColorScheme.Dark else "Light"
    except Exception:
        return "Light"


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)

    configure_windows()
    QCoreApplication.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings, True)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(argv)
    app.setApplicationName(branding.APP_NAME)
    app.setApplicationDisplayName(branding.APP_NAME)
    app.setApplicationVersion(branding.APP_VERSION)
    app.setOrganizationName(branding.COMPANY_NAME)
    app.setOrganizationDomain(branding.COMPANY_WEBSITE.replace("https://", "").rstrip("/"))
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())
    app.setFont(base_font())

    log_file = setup_logging()
    LOG.info("Starting %s %s", branding.APP_NAME, branding.APP_VERSION)
    LOG.info("Data directory: %s", paths.user_data_dir())

    # Ensure the database exists before any page reads settings from it.
    try:
        db.connect()
    except Exception as e:
        QMessageBox.critical(
            None, branding.APP_NAME,
            f"The settings database could not be opened.\n\n{e}\n\n"
            f"Check that you have write access to:\n{paths.user_data_dir()}")
        return 2

    palette = theme.resolve(resolve_theme_name())
    app.setPalette(theme.qt_palette(palette))
    app.setStyleSheet(theme.build_stylesheet(palette, config.get_float("ui_scale", 1.0)))

    splash = _splash(palette)
    if splash is not None:
        splash.show()
        app.processEvents()

    install_excepthook(app)

    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    if splash is not None:
        splash.finish(window)

    # files passed on the command line (Explorer "Open with", file association)
    incoming = [a for a in argv[1:] if not a.startswith("-") and Path(a).is_file()]
    if incoming:
        QTimer.singleShot(240, lambda: _open_incoming(window, incoming))

    LOG.info("Startup complete (log: %s)", log_file)
    return app.exec()


def _open_incoming(window, files: list[str]) -> None:
    pdfs = [f for f in files if f.lower().endswith(".pdf")]
    if len(pdfs) == 1:
        window.navigate("viewer", pdfs[0])
    elif len(pdfs) > 1:
        window.navigate("merge")
        page = window.current_page()
        if page is not None and hasattr(page, "handle_files"):
            page.handle_files(pdfs)
    else:
        window.navigate("dashboard")
        page = window.current_page()
        if page is not None and hasattr(page, "handle_files"):
            page.handle_files(files)


def _splash(palette) -> QSplashScreen | None:
    from PySide6.QtGui import (QBrush, QColor, QFont as QF, QLinearGradient,
                               QPainter)

    path = paths.asset(branding.SPLASH_FILE)
    if path.is_file():
        pm = QPixmap(str(path))
        if not pm.isNull():
            return QSplashScreen(pm, Qt.WindowStaysOnTopHint)

    pm = QPixmap(460, 260)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    grad = QLinearGradient(0, 0, 460, 260)
    grad.setColorAt(0.0, QColor(branding.BRAND_ACCENT))
    grad.setColorAt(1.0, QColor(branding.BRAND_ACCENT_ALT))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, 460, 260, 16, 16)

    p.setPen(QColor("#FFFFFF"))
    f = QF("Segoe UI", 24)
    f.setBold(True)
    p.setFont(f)
    p.drawText(38, 128, branding.APP_NAME)

    f2 = QF("Segoe UI", 11)
    p.setFont(f2)
    p.setPen(QColor(255, 255, 255, 205))
    p.drawText(40, 156, branding.APP_TAGLINE)
    p.drawText(40, 218, f"{branding.COMPANY_NAME}  ·  {branding.version_string()}")
    p.end()
    return QSplashScreen(pm, Qt.WindowStaysOnTopHint)


if __name__ == "__main__":
    raise SystemExit(main())
