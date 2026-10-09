"""Shared dialogs: keyboard shortcuts, first-run welcome, quick file search."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from app import branding, config, db, paths
from app.core import utils
from app.ui import components as C

SHORTCUTS = [
    ("Getting around", [
        ("Ctrl + K", "Search every tool by name"),
        ("Ctrl + B", "Collapse or expand the sidebar"),
        ("Alt + ←", "Go back to the previous screen"),
        ("Ctrl + 1", "Dashboard"),
        ("Ctrl + 2", "Merge PDF"),
        ("Ctrl + 3", "OCR PDF"),
        ("Ctrl + 4", "Compress PDF"),
        ("Ctrl + ,", "Settings"),
        ("F1", "This shortcuts list"),
        ("Esc", "Clear the search box"),
    ]),
    ("Files", [
        ("Ctrl + O", "Open a PDF in the viewer"),
        ("Ctrl + R", "Reopen a recent file"),
        ("Drag & drop", "Drop files anywhere in the window"),
        ("Double-click", "Preview a file in any file list"),
        ("Right-click", "More options on any file list or page grid"),
    ]),
    ("PDF viewer", [
        ("Ctrl + F", "Find text in the document"),
        ("Enter", "Jump to the next match"),
        ("Ctrl + scroll", "Zoom in and out"),
        ("+ / −", "Zoom in and out"),
        ("← / →", "Previous and next page"),
    ]),
    ("Page organiser", [
        ("Click, Shift+click", "Select a range of pages"),
        ("Ctrl + click", "Add a page to the selection"),
        ("Drag", "Move pages into a new order"),
        ("Ctrl + Z", "Undo"),
        ("Ctrl + Y", "Redo"),
        ("Delete", "Remove the selected pages"),
    ]),
    ("Appearance", [
        ("Ctrl + D", "Switch between light and dark"),
    ]),
]


class ShortcutsDialog(QDialog):
    """Keyboard reference, grouped by area."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.setMinimumSize(680, 620)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(14)

        head = QLabel("Keyboard shortcuts")
        head.setObjectName("PageTitle")
        lay.addWidget(head)

        sub = QLabel("Everything below works from any screen.")
        sub.setObjectName("Subtle")
        lay.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(14)

        for section, rows in SHORTCUTS:
            card = C.Card(section, shadow=False, flat=True)
            grid = QGridLayout()
            grid.setSpacing(7)
            grid.setColumnStretch(1, 1)
            for i, (keys, what) in enumerate(rows):
                k = QLabel(keys)
                k.setObjectName("Mono")
                k.setStyleSheet(
                    "padding:2px 8px;border:1px solid rgba(128,128,128,0.35);"
                    "border-radius:5px;")
                k.setAlignment(Qt.AlignCenter)
                grid.addWidget(k, i, 0)
                grid.addWidget(QLabel(what), i, 1)
            holder = QWidget()
            holder.setLayout(grid)
            card.add(holder)
            col.addWidget(card)

        col.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)


class WelcomeDialog(QDialog):
    """Shown once, on the first run, so the app does not open cold."""

    navigate = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Welcome to {branding.APP_NAME}")
        self.setMinimumWidth(660)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ---- branded header ----------------------------------------------
        header = QFrame()
        header.setObjectName("Hero")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(26, 22, 26, 22)
        hl.setSpacing(18)

        logo = QLabel()
        mark = paths.asset(branding.LOGO_MARK_FILE)
        if mark.is_file():
            pm = QPixmap(str(mark))
            if not pm.isNull():
                logo.setPixmap(pm.scaled(64, 64, Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))
        hl.addWidget(logo, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(4)
        t = QLabel(f"Welcome to {branding.APP_NAME}")
        t.setObjectName("HeroTitle")
        col.addWidget(t)
        s = QLabel(f"{branding.APP_TAGLINE}  ·  by {branding.COMPANY_NAME}")
        s.setObjectName("HeroText")
        col.addWidget(s)
        hl.addLayout(col, 1)
        lay.addWidget(header)

        # ---- body ---------------------------------------------------------
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(26, 20, 26, 20)
        bl.setSpacing(14)

        intro = QLabel(
            "Three things worth knowing before you start:")
        bl.addWidget(intro)

        for icon, title, text in [
            ("📂", "Drag files in from anywhere",
             "Drop PDFs onto any screen — the dashboard suggests what to do next."),
            ("🔒", "Nothing leaves this computer",
             branding.PRIVACY_STATEMENT),
            ("💾", "Your originals are safe",
             "Results are saved as new files (document_ocr.pdf). An original is "
             "only replaced if you explicitly ask for it."),
        ]:
            row = QHBoxLayout()
            row.setSpacing(12)
            ic = QLabel(icon)
            f = ic.font()
            f.setPointSize(16)
            ic.setFont(f)
            row.addWidget(ic, 0, Qt.AlignTop)
            c = QVBoxLayout()
            c.setSpacing(1)
            h = QLabel(title)
            hf = h.font()
            hf.setBold(True)
            h.setFont(hf)
            c.addWidget(h)
            d = QLabel(text)
            d.setObjectName("Subtle")
            d.setWordWrap(True)
            c.addWidget(d)
            row.addLayout(c, 1)
            holder = QWidget()
            holder.setLayout(row)
            bl.addWidget(holder)

        bl.addWidget(C.hline())

        start = QLabel("Start with one of these:")
        bl.addWidget(start)

        quick = QHBoxLayout()
        quick.setSpacing(8)
        for key, label in [("merge", "Merge PDFs"), ("ocr", "Make a scan searchable"),
                           ("compress", "Shrink a big PDF"),
                           ("organize", "Reorder pages")]:
            b = QPushButton(label)
            b.setObjectName("ChipButton")
            b.clicked.connect(lambda _=False, k=key: self._go(k))
            quick.addWidget(b)
        quick.addStretch(1)
        bl.addLayout(quick)
        lay.addWidget(body)

        # ---- footer -------------------------------------------------------
        footer = QHBoxLayout()
        footer.setContentsMargins(26, 0, 26, 20)
        self.dont_show = C.check("Don't show this again", True)
        footer.addWidget(self.dont_show)
        footer.addStretch(1)
        shortcuts = QPushButton("Keyboard shortcuts")
        shortcuts.setObjectName("Ghost")
        shortcuts.clicked.connect(self._shortcuts)
        footer.addWidget(shortcuts)
        close = QPushButton("Get started")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        lay.addLayout(footer)

    def _go(self, key: str) -> None:
        self.navigate.emit(key)
        self.accept()

    def _shortcuts(self) -> None:
        ShortcutsDialog(self).exec()

    def accept(self) -> None:
        if self.dont_show.isChecked():
            config.set("first_run_complete", True)
        super().accept()


class RecentFilesDialog(QDialog):
    """Quick reopen, with type-ahead filtering."""

    file_chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recent files")
        self.setMinimumSize(620, 480)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Type to filter…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)

        self.list = QListWidget()
        self.list.itemActivated.connect(self._choose)
        self.list.itemDoubleClicked.connect(self._choose)
        lay.addWidget(self.list, 1)

        self.empty = QLabel(
            "Nothing here yet — files you process will appear in this list.\n\n"
            "If you have turned history off in Settings, recent files are not "
            "recorded.")
        self.empty.setObjectName("Subtle")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty)

        row = QHBoxLayout()
        clear = QPushButton("Clear list")
        clear.setObjectName("Ghost")
        clear.clicked.connect(self._clear)
        row.addWidget(clear)
        row.addStretch(1)
        open_btn = QPushButton("Open")
        open_btn.setObjectName("Primary")
        open_btn.clicked.connect(lambda: self._choose(self.list.currentItem()))
        row.addWidget(open_btn)
        close = QPushButton("Close")
        close.setObjectName("Ghost")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        lay.addLayout(row)

        self._rows: list[tuple[str, str, str]] = []
        self._load()

    def _load(self) -> None:
        self._rows = []
        for r in db.get_recent(limit=50):
            path = r["path"]
            if not Path(path).exists():
                continue
            when = (r["last_used"] or "")[:16].replace("T", "  ")
            self._rows.append((path, r["name"], f"{r['last_op'] or '—'}   ·   {when}"))
        self._filter()

    def _filter(self) -> None:
        q = self.search.text().strip().lower()
        self.list.clear()
        shown = 0
        for path, name, detail in self._rows:
            if q and q not in name.lower() and q not in path.lower():
                continue
            item = QListWidgetItem(f"{name}\n{detail}")
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            item.setSizeHint(QSize(0, 52))
            self.list.addItem(item)
            shown += 1
        self.list.setVisible(shown > 0)
        self.empty.setVisible(shown == 0)
        if shown:
            self.list.setCurrentRow(0)

    def _choose(self, item) -> None:
        if item is None:
            return
        path = item.data(Qt.UserRole)
        if path:
            self.file_chosen.emit(path)
            self.accept()

    def _clear(self) -> None:
        db.clear_recent()
        self._load()
