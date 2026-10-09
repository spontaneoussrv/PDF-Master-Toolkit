"""Full-window PDF viewer with a quick-action rail."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from app.core import utils
from app.core.pdfbase import needs_password
from app.ui import components as C
from app.ui.pages.base import BasePage
from app.ui.viewer import PdfViewer


class ViewerPage(BasePage):
    TITLE = "PDF Viewer"
    SUBTITLE = "Read, search and copy from any PDF"

    def __init__(self, window=None, parent=None):
        super().__init__(window, parent)
        self.current: Path | None = None
        self.password: str | None = None

    def build(self) -> None:
        # this page fills the window rather than scrolling
        self._root.removeWidget(self.scroll)
        self.scroll.setParent(None)

        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(12)

        self.empty = C.DropZone("Drop a PDF here to read it",
                                hint="or click to browse")
        self.empty.files_dropped.connect(lambda p: self.open_file(p[0]))
        lay.addWidget(self.empty)

        self.viewer = PdfViewer()
        self.viewer.setVisible(False)
        self.viewer.error.connect(lambda m: self.toast(m, "error"))
        self.viewer.text_copied.connect(
            lambda t: self.toast(f"Copied {len(t.split())} words to the clipboard"))
        self.viewer.page_changed.connect(self._on_page_changed)
        lay.addWidget(self.viewer, 1)

        self.actions = self._build_action_rail()
        self.actions.setVisible(False)
        lay.addWidget(self.actions)

        self._root.addWidget(container, 1)

    def _build_action_rail(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Card")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(8)

        self.doc_label = QLabel("")
        self.doc_label.setObjectName("Subtle")
        lay.addWidget(self.doc_label, 1)

        for key, text in [("ocr", "OCR"), ("compress", "Compress"),
                          ("organize", "Organise"), ("extract_pages", "Extract"),
                          ("watermark", "Watermark"), ("protect", "Protect"),
                          ("extract_text", "Extract text")]:
            btn = QPushButton(text)
            btn.setObjectName("ChipButton")
            btn.clicked.connect(lambda _=False, k=key: self._send_to(k))
            lay.addWidget(btn)

        close = QPushButton("Close")
        close.setObjectName("Ghost")
        close.clicked.connect(self.close_file)
        lay.addWidget(close)
        return bar

    # -- opening -----------------------------------------------------------
    def receive(self, payload) -> None:
        if isinstance(payload, (str, Path)):
            self.ensure_built()
            self.open_file(payload)

    def handle_files(self, paths: Sequence[str]) -> None:
        for p in paths:
            if str(p).lower().endswith(".pdf"):
                self.open_file(p)
                return

    def open_file(self, path: str | Path) -> None:
        p = Path(path)
        if not p.is_file():
            self.toast(f"{p.name} was not found", "error")
            return

        password = None
        if needs_password(p):
            password = self._ask_password(p)
            if password is None:
                return

        if not self.viewer.load(p, password):
            return

        self.current = p
        self.password = password
        self.empty.setVisible(False)
        self.viewer.setVisible(True)
        self.actions.setVisible(True)
        self.doc_label.setText(
            f"📄  {p.name}      {self.viewer.page_count} pages      "
            f"{utils.human_size(utils.file_size(p))}")
        self.status_message.emit(f"Opened {p.name}")

    def _ask_password(self, path: Path) -> str | None:
        from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox
        from app.core.pdfbase import open_pdf

        for attempt in range(3):
            prompt = f"'{path.name}' is password protected.\nEnter the password:"
            if attempt:
                prompt = "That password was not accepted.\n\n" + prompt
            pw, ok = QInputDialog.getText(self, "Password required", prompt,
                                          QLineEdit.Password)
            if not ok:
                return None
            try:
                doc = open_pdf(path, pw)
                doc.close()
                return pw
            except Exception:
                continue
        QMessageBox.information(
            self, "Cannot open",
            "The password was not accepted. PDF Master Toolkit does not attempt "
            "to recover PDF passwords.")
        return None

    def close_file(self) -> None:
        self.viewer.close_document()
        self.viewer.setVisible(False)
        self.actions.setVisible(False)
        self.empty.setVisible(True)
        self.current = None

    def _on_page_changed(self, index: int) -> None:
        if self.current:
            self.status_message.emit(
                f"{self.current.name} — page {index + 1} of {self.viewer.page_count}")

    def _send_to(self, key: str) -> None:
        if not self.current:
            return
        path = str(self.current)
        self.go(key)
        if self.window_ref is not None:
            page = self.window_ref.current_page()
            if page is not None and hasattr(page, "handle_files"):
                page.handle_files([path])

    def on_hide(self) -> None:
        pass
