"""
Reusable UI building blocks.

Every tool screen is assembled from these, which is what makes 30+ modules feel
like one product rather than 30 separate dialogs. Objects set ``objectName`` to
match the selectors in :mod:`app.theme`, so restyling happens centrally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QSize, Qt,
                            QTimer, Signal)
from PySide6.QtGui import (QColor, QCursor, QDragEnterEvent, QDropEvent, QFont,
                           QIcon, QPainter, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QColorDialog, QComboBox, QDoubleSpinBox,
                               QFileDialog, QFrame, QGraphicsDropShadowEffect,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QSpinBox, QTextEdit, QVBoxLayout,
                               QWidget)

from app import config, theme
from app.core import utils
from app.core.pdf_ops import PdfInfo, inspect

SPACE = theme.SPACE


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("Separator")
    f.setFrameShape(QFrame.HLine)
    f.setFixedHeight(1)
    return f


def vline() -> QFrame:
    f = QFrame()
    f.setObjectName("VSeparator")
    f.setFrameShape(QFrame.VLine)
    f.setFixedWidth(1)
    return f


def spacer(w: int = 0, h: int = 0) -> QWidget:
    s = QWidget()
    s.setFixedSize(QSize(w, h) if (w or h) else QSize(0, 0))
    if not w:
        s.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return s


def label(text: str, obj: str = "", wrap: bool = False, bold: bool = False) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    if bold:
        f = lb.font()
        f.setBold(True)
        lb.setFont(f)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse if not wrap
                               else Qt.TextSelectableByMouse)
    return lb


def title_label(text: str) -> QLabel:
    return label(text, "CardTitle")


def muted(text: str, wrap: bool = True) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("Subtle")
    lb.setWordWrap(wrap)
    return lb


def elevate(widget: QWidget, blur: int = 22, alpha: int = 26, dy: int = 3) -> None:
    """Soft drop shadow — the single strongest cue that a card is a surface."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setColor(QColor(16, 24, 40, alpha))
    effect.setOffset(0, dy)
    widget.setGraphicsEffect(effect)


# ---------------------------------------------------------------------------
# cards
# ---------------------------------------------------------------------------

class Card(QFrame):
    """A titled surface. ``body`` is the layout callers add content to."""

    def __init__(self, title: str = "", subtitle: str = "", parent=None,
                 flat: bool = False, padding: int = 18, shadow: bool = True):
        super().__init__(parent)
        self.setObjectName("CardFlat" if flat else "Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(padding, padding, padding, padding)
        outer.setSpacing(12)

        self.header = QHBoxLayout()
        self.header.setSpacing(10)
        self._title_box = QVBoxLayout()
        self._title_box.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("CardSubtitle")
        self.subtitle_label.setWordWrap(True)

        self._title_box.addWidget(self.title_label)
        self._title_box.addWidget(self.subtitle_label)
        self.header.addLayout(self._title_box, 1)

        self.title_label.setVisible(bool(title))
        self.subtitle_label.setVisible(bool(subtitle))
        if title or subtitle:
            outer.addLayout(self.header)

        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        outer.addLayout(self.body, 1)

        if shadow and not flat:
            elevate(self)

    def add_header_widget(self, widget: QWidget) -> None:
        self.header.addWidget(widget, 0, Qt.AlignRight | Qt.AlignVCenter)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)
        self.title_label.setVisible(bool(text))

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))


class StatCard(QFrame):
    """Dashboard metric tile."""

    clicked = Signal()

    def __init__(self, label_text: str, value: str = "0", icon: str = "",
                 accent: str = "", delta: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setCursor(QCursor(Qt.PointingHandCursor))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(6)

        top = QHBoxLayout()
        self.icon_label = QLabel(icon)
        f = self.icon_label.font()
        f.setPointSize(15)
        self.icon_label.setFont(f)
        if accent:
            self.icon_label.setStyleSheet(f"color:{accent};")
        top.addWidget(self.icon_label)
        top.addStretch(1)
        self.delta_label = QLabel(delta)
        self.delta_label.setObjectName("StatDelta")
        self.delta_label.setVisible(bool(delta))
        top.addWidget(self.delta_label)
        lay.addLayout(top)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("StatValue")
        lay.addWidget(self.value_label)

        self.name_label = QLabel(label_text)
        self.name_label.setObjectName("StatLabel")
        lay.addWidget(self.name_label)

        elevate(self, blur=16, alpha=20, dy=2)
        self.setMinimumHeight(122)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)

    def set_delta(self, text: str) -> None:
        self.delta_label.setText(text)
        self.delta_label.setVisible(bool(text))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Badge(QLabel):
    KIND = {"": "Badge", "accent": "BadgeAccent", "success": "BadgeSuccess",
            "warning": "BadgeWarning", "danger": "BadgeDanger"}

    def __init__(self, text: str = "", kind: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName(self.KIND.get(kind, "Badge"))
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)

    def set_kind(self, kind: str) -> None:
        self.setObjectName(self.KIND.get(kind, "Badge"))
        self.style().unpolish(self)
        self.style().polish(self)


class Banner(QFrame):
    """Inline notice. Used for disclaimers the product must state plainly."""

    OBJ = {"info": "InfoBanner", "warning": "WarnBanner",
           "danger": "DangerBanner", "success": "SuccessBanner"}
    ICON = {"info": "ⓘ", "warning": "⚠", "danger": "⛔", "success": "✓"}

    def __init__(self, text: str, kind: str = "info", parent=None,
                 closable: bool = False):
        super().__init__(parent)
        self.setObjectName(self.OBJ.get(kind, "InfoBanner"))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        icon = QLabel(self.ICON.get(kind, "ⓘ"))
        icon.setAlignment(Qt.AlignTop)
        lay.addWidget(icon, 0, Qt.AlignTop)

        self.text_label = QLabel(text)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.text_label, 1)

        if closable:
            btn = QPushButton("✕")
            btn.setObjectName("Ghost")
            btn.setFixedSize(22, 22)
            btn.clicked.connect(self.hide)
            lay.addWidget(btn, 0, Qt.AlignTop)

    def set_text(self, text: str) -> None:
        self.text_label.setText(text)

    def set_kind(self, kind: str) -> None:
        self.setObjectName(self.OBJ.get(kind, "InfoBanner"))
        self.style().unpolish(self)
        self.style().polish(self)


# ---------------------------------------------------------------------------
# drop zone
# ---------------------------------------------------------------------------

class DropZone(QFrame):
    """Large drag-and-drop target that also opens a file dialog when clicked."""

    files_dropped = Signal(list)

    def __init__(self, text: str = "Drop PDF files here", hint: str = "",
                 extensions: Sequence[str] = (".pdf",), parent=None,
                 icon: str = "⬇", compact: bool = False, folders: bool = False):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setProperty("hover", "false")
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.extensions = tuple(e.lower() for e in extensions)
        self.allow_folders = folders

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18 if compact else 30, 20, 18 if compact else 30)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignCenter)

        self.icon_label = QLabel(icon)
        self.icon_label.setObjectName("DropZoneIcon")
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setVisible(not compact)
        lay.addWidget(self.icon_label)

        self.title = QLabel(text)
        self.title.setObjectName("DropZoneTitle")
        self.title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.title)

        default_hint = "or click to browse — " + ", ".join(
            e.lstrip(".").upper() for e in self.extensions[:5])
        self.hint = QLabel(hint or default_hint)
        self.hint.setObjectName("DropZoneHint")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        self.setMinimumHeight(96 if compact else 168)

    # -- drag/drop ---------------------------------------------------------
    def _accepted(self, paths: Iterable[str]) -> list[str]:
        out = []
        for p in paths:
            path = Path(p)
            if path.is_dir():
                if self.allow_folders:
                    out.append(str(path))
                else:
                    for child in sorted(path.rglob("*")):
                        if child.suffix.lower() in self.extensions:
                            out.append(str(child))
            elif path.suffix.lower() in self.extensions:
                out.append(str(path))
        return out

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            if self._accepted(paths):
                event.acceptProposedAction()
                self._set_hover(True)
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_hover(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_hover(False)
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        accepted = self._accepted(paths)
        if accepted:
            self.files_dropped.emit(accepted)
            event.acceptProposedAction()

    def _set_hover(self, on: bool) -> None:
        self.setProperty("hover", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.browse()
        super().mouseReleaseEvent(event)

    def browse(self) -> None:
        patterns = " ".join(f"*{e}" for e in self.extensions)
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select files", _last_dir(), f"Supported files ({patterns});;All files (*.*)")
        if files:
            _remember_dir(files[0])
            self.files_dropped.emit(files)


def _last_dir() -> str:
    return str(config.get("last_browse_dir", "") or Path.home())


def _remember_dir(path: str | Path) -> None:
    try:
        config.set("last_browse_dir", str(Path(path).parent))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# file list
# ---------------------------------------------------------------------------

class FileListWidget(QWidget):
    """
    Ordered file list with drag-to-reorder, drag-and-drop import, page counts
    and per-row page-range entry. This is the backbone of Merge, Batch and
    Images to PDF.
    """

    changed = Signal()
    selection_changed = Signal()
    open_requested = Signal(str)

    def __init__(self, extensions: Sequence[str] = (".pdf",), parent=None,
                 show_ranges: bool = False, allow_duplicates: bool = True,
                 reorderable: bool = True):
        super().__init__(parent)
        self.extensions = tuple(e.lower() for e in extensions)
        self.show_ranges = show_ranges
        self.allow_duplicates = allow_duplicates
        self._info: dict[str, PdfInfo] = {}
        self._ranges: dict[str, str] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # toolbar
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.add_btn = QPushButton("＋  Add files")
        self.add_btn.clicked.connect(self.browse)
        self.folder_btn = QPushButton("Add folder")
        self.folder_btn.setObjectName("Ghost")
        self.folder_btn.clicked.connect(self.browse_folder)
        self.up_btn = QPushButton("↑")
        self.up_btn.setObjectName("Ghost")
        self.up_btn.setFixedWidth(34)
        self.up_btn.setToolTip("Move up")
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn = QPushButton("↓")
        self.down_btn.setObjectName("Ghost")
        self.down_btn.setFixedWidth(34)
        self.down_btn.setToolTip("Move down")
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setObjectName("Ghost")
        self.remove_btn.clicked.connect(self.remove_selected)
        self.clear_btn = QPushButton("Clear all")
        self.clear_btn.setObjectName("Ghost")
        self.clear_btn.clicked.connect(self.clear)

        bar.addWidget(self.add_btn)
        bar.addWidget(self.folder_btn)
        bar.addStretch(1)
        if reorderable:
            bar.addWidget(self.up_btn)
            bar.addWidget(self.down_btn)
        bar.addWidget(self.remove_btn)
        bar.addWidget(self.clear_btn)
        lay.addLayout(bar)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setAlternatingRowColors(False)
        self.list.setAcceptDrops(True)
        self.list.setDragEnabled(reorderable)
        self.list.setDragDropMode(QAbstractItemView.DragDrop if reorderable
                                  else QAbstractItemView.DropOnly)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        self.list.itemSelectionChanged.connect(self.selection_changed.emit)
        self.list.model().rowsMoved.connect(lambda *a: self.changed.emit())
        self.list.setMinimumHeight(180)
        lay.addWidget(self.list, 1)

        self.summary = QLabel("No files added yet")
        self.summary.setObjectName("Subtle")
        lay.addWidget(self.summary)

        # accept external drops onto the list itself
        self.list.dragEnterEvent = self._drag_enter          # type: ignore[assignment]
        self.list.dragMoveEvent = self._drag_move            # type: ignore[assignment]
        self.list.dropEvent = self._drop                     # type: ignore[assignment]

    # -- drag/drop ---------------------------------------------------------
    def _drag_enter(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            QListWidget.dragEnterEvent(self.list, event)

    def _drag_move(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            QListWidget.dragMoveEvent(self.list, event)

    def _drop(self, event):
        if event.mimeData().hasUrls():
            paths = []
            for u in event.mimeData().urls():
                p = Path(u.toLocalFile())
                if p.is_dir():
                    paths += [str(c) for c in sorted(p.rglob("*"))
                              if c.suffix.lower() in self.extensions]
                elif p.suffix.lower() in self.extensions:
                    paths.append(str(p))
            if paths:
                self.add_files(paths)
                event.acceptProposedAction()
                return
        QListWidget.dropEvent(self.list, event)
        self.changed.emit()

    # -- content -----------------------------------------------------------
    def browse(self) -> None:
        patterns = " ".join(f"*{e}" for e in self.extensions)
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add files", _last_dir(), f"Supported files ({patterns});;All files (*.*)")
        if files:
            _remember_dir(files[0])
            self.add_files(files)

    def browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add every file in a folder", _last_dir())
        if not folder:
            return
        found = [str(p) for p in sorted(Path(folder).rglob("*"))
                 if p.suffix.lower() in self.extensions]
        if not found:
            QMessageBox.information(self, "Nothing found",
                                    "That folder contains no supported files.")
            return
        _remember_dir(found[0])
        self.add_files(found)

    def add_files(self, paths: Sequence[str | Path]) -> None:
        existing = set(self.paths())
        added = 0
        for p in paths:
            path = Path(p)
            if not path.is_file():
                continue
            key = str(path)
            if not self.allow_duplicates and key in existing:
                continue
            item = QListWidgetItem()
            item.setData(Qt.UserRole, key)
            item.setSizeHint(QSize(0, 54 if self.show_ranges else 46))
            self.list.addItem(item)
            self.list.setItemWidget(item, self._row_widget(path))
            existing.add(key)
            added += 1
        if added:
            self._refresh_summary()
            self.changed.emit()
            QTimer.singleShot(30, lambda: self._load_info_async())

    def _row_widget(self, path: Path) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(10)

        icon = QLabel("📄" if path.suffix.lower() == ".pdf" else "🖼")
        lay.addWidget(icon)

        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel(path.name)
        name.setToolTip(str(path))
        nf = name.font()
        nf.setWeight(QFont.DemiBold)
        name.setFont(nf)
        col.addWidget(name)

        meta = QLabel(utils.human_size(utils.file_size(path)))
        meta.setObjectName("Subtle")
        col.addWidget(meta)
        lay.addLayout(col, 1)

        w.setProperty("meta_label", meta)

        if self.show_ranges:
            rng = QLineEdit()
            rng.setPlaceholderText("All pages")
            rng.setFixedWidth(130)
            rng.setToolTip("Leave blank for all pages, or enter 1-5, 2,4,7-9, odd, even")
            rng.textChanged.connect(
                lambda text, key=str(path): self._set_range(key, text))
            lay.addWidget(rng)
            w.setProperty("range_edit", rng)

        return w

    def _set_range(self, key: str, text: str) -> None:
        self._ranges[key] = text.strip()
        self.changed.emit()

    def _load_info_async(self) -> None:
        """Probe page counts a few rows at a time so the UI never blocks."""
        pending = [p for p in self.paths()
                   if p not in self._info and p.lower().endswith(".pdf")]
        if not pending:
            self._refresh_summary()
            return
        for p in pending[:4]:
            try:
                self._info[p] = inspect(p)
            except Exception:
                continue
        self._apply_info()
        if len(pending) > 4:
            QTimer.singleShot(10, self._load_info_async)
        else:
            self._refresh_summary()

    def _apply_info(self) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            key = item.data(Qt.UserRole)
            info = self._info.get(key)
            if not info:
                continue
            w = self.list.itemWidget(item)
            if w is None:
                continue
            meta = w.property("meta_label")
            if meta is None:
                continue
            if info.error:
                meta.setText(f"{utils.human_size(info.size)} · {info.error}")
            else:
                bits = [f"{info.pages} page{'s' if info.pages != 1 else ''}",
                        utils.human_size(info.size)]
                if info.page_size:
                    bits.append(info.page_size)
                meta.setText("  ·  ".join(bits))

    def _refresh_summary(self) -> None:
        n = self.list.count()
        if n == 0:
            self.summary.setText("No files added yet")
            return
        pages = sum(i.pages for i in self._info.values()
                    if str(i.path) in set(self.paths()))
        size = sum(utils.file_size(p) for p in self.paths())
        parts = [f"{n} file{'s' if n != 1 else ''}"]
        if pages:
            parts.append(f"{pages} pages")
        parts.append(utils.human_size(size))
        self.summary.setText("  ·  ".join(parts))

    # -- ops ---------------------------------------------------------------
    def _move(self, delta: int) -> None:
        rows = sorted((self.list.row(i) for i in self.list.selectedItems()),
                      reverse=delta > 0)
        if not rows:
            return
        for row in rows:
            new = row + delta
            if not (0 <= new < self.list.count()):
                continue
            item = self.list.takeItem(row)
            key = item.data(Qt.UserRole)
            self.list.insertItem(new, item)
            self.list.setItemWidget(item, self._row_widget(Path(key)))
            item.setSelected(True)
        self._apply_info()
        self.changed.emit()

    def remove_selected(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self._refresh_summary()
        self.changed.emit()

    def clear(self) -> None:
        self.list.clear()
        self._ranges.clear()
        self._refresh_summary()
        self.changed.emit()

    def sort_by_name(self) -> None:
        paths = sorted(self.paths(), key=lambda p: Path(p).name.lower())
        self._rebuild(paths)

    def sort_by_date(self) -> None:
        paths = sorted(self.paths(), key=lambda p: Path(p).stat().st_mtime
                       if Path(p).exists() else 0)
        self._rebuild(paths)

    def reverse(self) -> None:
        self._rebuild(list(reversed(self.paths())))

    def _rebuild(self, paths: Sequence[str]) -> None:
        self.list.clear()
        self.add_files(paths)

    def _context_menu(self, pos: QPoint) -> None:
        item = self.list.itemAt(pos)
        menu = QMenu(self)
        if item:
            key = item.data(Qt.UserRole)
            menu.addAction("Open", lambda: utils.open_path(key))
            menu.addAction("Show in folder", lambda: utils.reveal_in_explorer(key))
            menu.addAction("Preview", lambda: self.open_requested.emit(key))
            menu.addSeparator()
            menu.addAction("Remove", self.remove_selected)
            menu.addSeparator()
        menu.addAction("Sort by name", self.sort_by_name)
        menu.addAction("Sort by date", self.sort_by_date)
        menu.addAction("Reverse order", self.reverse)
        menu.addSeparator()
        menu.addAction("Clear all", self.clear)
        menu.exec(self.list.mapToGlobal(pos))

    def _on_double_click(self, item: QListWidgetItem) -> None:
        self.open_requested.emit(item.data(Qt.UserRole))

    # -- access ------------------------------------------------------------
    def paths(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def selected_paths(self) -> list[str]:
        return [i.data(Qt.UserRole) for i in self.list.selectedItems()]

    def count(self) -> int:
        return self.list.count()

    def info_for(self, path: str) -> PdfInfo | None:
        return self._info.get(path)

    def range_for(self, path: str) -> str:
        return self._ranges.get(path, "")

    def total_pages(self) -> int:
        return sum(i.pages for i in self._info.values())


# ---------------------------------------------------------------------------
# form controls
# ---------------------------------------------------------------------------

class FormRow(QWidget):
    """Label + control on one line, with an optional hint underneath."""

    def __init__(self, text: str, widget: QWidget, hint: str = "",
                 label_width: int = 168, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.label = QLabel(text)
        self.label.setMinimumWidth(label_width)
        self.label.setWordWrap(True)
        row.addWidget(self.label, 0, Qt.AlignTop)
        row.addWidget(widget, 1)
        outer.addLayout(row)

        if hint:
            h = QLabel(hint)
            h.setObjectName("Subtle")
            h.setWordWrap(True)
            h.setContentsMargins(label_width + 12, 0, 0, 0)
            outer.addWidget(h)

        self.widget = widget


def form(*rows: QWidget) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(11)
    for r in rows:
        lay.addWidget(r)
    return lay


class PageRangeEdit(QLineEdit):
    """Page-range entry that validates live against a known page count."""

    def __init__(self, total: int = 0, parent=None, default: str = "all"):
        super().__init__(parent)
        self.total = total
        self.setPlaceholderText("all, 1-5, 1,3,7-9, odd, even")
        self.setText(default)
        self.setToolTip(
            "Examples:\n"
            "  all           every page\n"
            "  1-5           a range\n"
            "  1,3,7-9       a mix\n"
            "  odd / even    alternating pages\n"
            "  3-end         from a page to the end")
        self.textChanged.connect(self._validate)
        self._validate()

    def set_total(self, total: int) -> None:
        self.total = total
        self._validate()

    def _validate(self) -> None:
        ok = True
        if self.total > 0 and self.text().strip():
            ok = bool(utils.parse_page_ranges(self.text(), self.total))
        self.setProperty("error", "false" if ok else "true")
        self.style().unpolish(self)
        self.style().polish(self)
        if self.total > 0:
            n = len(utils.parse_page_ranges(self.text(), self.total))
            self.setToolTip(f"{n} page(s) selected of {self.total}"
                            if n else "This selection matches no pages")

    def pages(self) -> list[int]:
        return utils.parse_page_ranges(self.text(), self.total)

    def is_valid(self) -> bool:
        return not self.total or bool(self.pages())


class PathPicker(QWidget):
    """Line edit + Browse button for a file or folder."""

    changed = Signal(str)

    def __init__(self, mode: str = "open", filters: str = "All files (*.*)",
                 placeholder: str = "", parent=None, default: str = ""):
        super().__init__(parent)
        self.mode = mode                      # open | save | folder
        self.filters = filters
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.edit = QLineEdit(default)
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.changed.emit)
        lay.addWidget(self.edit, 1)

        self.button = QPushButton("Browse…")
        self.button.setObjectName("Ghost")
        self.button.clicked.connect(self.browse)
        lay.addWidget(self.button)

    def browse(self) -> None:
        start = self.edit.text().strip() or _last_dir()
        if self.mode == "folder":
            path = QFileDialog.getExistingDirectory(self, "Select folder", start)
        elif self.mode == "save":
            path, _ = QFileDialog.getSaveFileName(self, "Save as", start, self.filters)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select file", start, self.filters)
        if path:
            self.edit.setText(path)
            _remember_dir(path)

    def text(self) -> str:
        return self.edit.text().strip()

    def path(self) -> Path | None:
        t = self.text()
        return Path(t) if t else None

    def setText(self, value: str) -> None:
        self.edit.setText(value)


class ColorPicker(QPushButton):
    """Swatch button that opens the system colour dialog."""

    color_changed = Signal(str)

    def __init__(self, color: str = "#FF0000", parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(78, 32)
        self.clicked.connect(self._pick)
        self._apply()

    def _apply(self) -> None:
        text_color = theme.readable_on(self._color)
        self.setText(self._color.upper())
        self.setStyleSheet(
            f"QPushButton{{background:{self._color};color:{text_color};"
            f"border:1px solid rgba(0,0,0,0.18);border-radius:6px;"
            f"font-size:11px;font-weight:700;}}")

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, "Choose colour")
        if chosen.isValid():
            self.set_color(chosen.name())

    def set_color(self, value: str) -> None:
        self._color = value
        self._apply()
        self.color_changed.emit(value)

    def color(self) -> str:
        return self._color


class PositionGrid(QWidget):
    """3×3 anchor selector — far clearer than a nine-item combo box."""

    changed = Signal(str)

    def __init__(self, value: str = "center", parent=None):
        super().__init__(parent)
        from app.core.annotate import POSITIONS

        self._value = value
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        self.buttons: dict[str, QPushButton] = {}

        for idx, pos in enumerate(POSITIONS):
            btn = QPushButton()
            btn.setObjectName("ChipButton")
            btn.setCheckable(True)
            btn.setFixedSize(38, 30)
            btn.setToolTip(pos.replace("-", " ").title())
            btn.clicked.connect(lambda _, p=pos: self.set_value(p))
            grid.addWidget(btn, idx // 3, idx % 3)
            self.buttons[pos] = btn
        self.set_value(value)

    def set_value(self, value: str) -> None:
        self._value = value
        for pos, btn in self.buttons.items():
            btn.setChecked(pos == value)
            btn.setText("●" if pos == value else "")
        self.changed.emit(value)

    def value(self) -> str:
        return self._value


class SegmentedControl(QWidget):
    """Row of mutually exclusive chips."""

    changed = Signal(str)

    def __init__(self, options: Sequence[tuple[str, str]], value: str = "",
                 parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.buttons: dict[str, QPushButton] = {}
        for val, text in options:
            btn = QPushButton(text)
            btn.setObjectName("ChipButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, v=val: self.set_value(v))
            lay.addWidget(btn)
            self.buttons[str(val)] = btn
        lay.addStretch(1)
        self._value = ""
        self.set_value(value or (str(options[0][0]) if options else ""))

    def set_value(self, value: str) -> None:
        value = str(value)
        if value not in self.buttons:
            return
        self._value = value
        for v, b in self.buttons.items():
            b.setChecked(v == value)
        self.changed.emit(value)

    def value(self) -> str:
        return self._value


def spin(value: int, lo: int, hi: int, suffix: str = "", step: int = 1) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    s.setSingleStep(step)
    if suffix:
        s.setSuffix(suffix)
    s.setMinimumWidth(110)
    return s


def dspin(value: float, lo: float, hi: float, suffix: str = "",
          step: float = 0.1, decimals: int = 2) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    if suffix:
        s.setSuffix(suffix)
    s.setMinimumWidth(110)
    return s


def combo(options: Sequence[tuple], current=None) -> QComboBox:
    c = QComboBox()
    for value, text in options:
        c.addItem(str(text), value)
    if current is not None:
        idx = c.findData(current)
        if idx >= 0:
            c.setCurrentIndex(idx)
    c.setMinimumWidth(160)
    return c


def check(text: str, checked: bool = False, tip: str = "") -> QCheckBox:
    cb = QCheckBox(text)
    cb.setChecked(checked)
    if tip:
        cb.setToolTip(tip)
    return cb


# ---------------------------------------------------------------------------
# output settings
# ---------------------------------------------------------------------------

class OutputPanel(Card):
    """Consistent 'where does the result go' block used by every tool."""

    def __init__(self, default_suffix: str = "processed", extension: str = ".pdf",
                 parent=None, folder_mode: bool = False):
        super().__init__("Output", parent=parent)
        self.default_suffix = default_suffix
        self.extension = extension
        self.folder_mode = folder_mode
        self._source: Path | None = None

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Output file name")
        self.dir_picker = PathPicker("folder", placeholder="Same folder as the original")

        if not folder_mode:
            self.body.addWidget(FormRow("File name", self.name_edit))
        self.body.addWidget(FormRow("Save to", self.dir_picker))

        self.open_after = check("Open the result when finished",
                                config.get_bool("open_output_after", True))
        self.reveal_after = check("Show the containing folder", False)
        row = QHBoxLayout()
        row.addWidget(self.open_after)
        row.addWidget(self.reveal_after)
        row.addStretch(1)
        self.body.addLayout(row)

        self.note = QLabel("")
        self.note.setObjectName("Subtle")
        self.note.setWordWrap(True)
        self.body.addWidget(self.note)
        self._update_note()
        self.name_edit.textChanged.connect(self._update_note)
        self.dir_picker.changed.connect(lambda _: self._update_note())

    def set_source(self, path: str | Path | None, suffix: str | None = None) -> None:
        self._source = Path(path) if path else None
        if self._source and not self.name_edit.text().strip():
            sfx = suffix if suffix is not None else self.default_suffix
            stem = f"{self._source.stem}_{sfx}" if sfx else self._source.stem
            self.name_edit.setText(stem)
        self._update_note()

    def resolved(self, source: str | Path | None = None,
                 suffix: str | None = None, extension: str | None = None) -> Path:
        """The final output path, honouring the duplicate-safe naming rule."""
        src = Path(source) if source else self._source
        ext = extension or self.extension
        folder = self.dir_picker.path()
        if folder is None:
            folder = src.parent if src else Path.home()
        folder.mkdir(parents=True, exist_ok=True)

        if self.folder_mode:
            return folder

        stem = self.name_edit.text().strip()
        if not stem:
            sfx = suffix if suffix is not None else self.default_suffix
            stem = f"{src.stem}_{sfx}" if src else "output"
        stem = utils.sanitize_filename(stem)
        if stem.lower().endswith(ext.lower()):
            stem = stem[: -len(ext)]
        target = folder / f"{stem}{ext}"
        if src and utils.is_same_file(target, src):
            target = folder / f"{stem}_out{ext}"
        return utils.resolve_collision(target)

    def output_dir(self) -> Path:
        folder = self.dir_picker.path()
        if folder is None:
            folder = self._source.parent if self._source else Path.home()
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _update_note(self) -> None:
        if self.folder_mode:
            self.note.setText(
                f"Files are written to {self.output_dir()}" if self._source or self.dir_picker.text()
                else "Files are written next to the originals.")
            return
        if not self._source and not self.name_edit.text().strip():
            self.note.setText("The original file is never overwritten. "
                              "A number is added if the name already exists.")
            return
        try:
            target = self.resolved()
            self.note.setText(f"Saving as  {target.name}  in  {target.parent}")
        except Exception:
            self.note.setText("The original file is never overwritten.")


# ---------------------------------------------------------------------------
# run bar
# ---------------------------------------------------------------------------

class RunBar(QFrame):
    """Primary action + progress + cancel. Present on every tool screen."""

    run_clicked = Signal()
    cancel_clicked = Signal()
    pause_clicked = Signal()

    def __init__(self, action_text: str = "Start", parent=None,
                 show_pause: bool = False):
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(10)

        self.status = QLabel("Ready")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setObjectName("Ghost")
        self.pause_btn.setVisible(False)
        self.pause_btn.clicked.connect(self.pause_clicked.emit)
        row.addWidget(self.pause_btn)
        self._show_pause = show_pause

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("Ghost")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_clicked.emit)
        row.addWidget(self.cancel_btn)

        self.run_btn = QPushButton(action_text)
        self.run_btn.setObjectName("PrimaryLarge")
        self.run_btn.setMinimumWidth(168)
        self.run_btn.clicked.connect(self.run_clicked.emit)
        row.addWidget(self.run_btn)
        lay.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        elevate(self, blur=18, alpha=22, dy=2)

    def set_running(self, running: bool) -> None:
        self.run_btn.setEnabled(not running)
        self.cancel_btn.setVisible(running)
        self.pause_btn.setVisible(running and self._show_pause)
        self.progress.setVisible(running)
        if not running:
            self.progress.setValue(0)
            self.pause_btn.setText("Pause")

    def set_progress(self, value: int, message: str = "") -> None:
        self.progress.setValue(int(value))
        if message:
            self.status.setText(message)

    def set_status(self, message: str, kind: str = "") -> None:
        self.status.setText(message)
        self.status.setObjectName({"error": "Danger", "success": "Success",
                                   "warning": "Warning"}.get(kind, "Muted"))
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def set_action_text(self, text: str) -> None:
        self.run_btn.setText(text)

    def set_enabled(self, enabled: bool) -> None:
        self.run_btn.setEnabled(enabled)

    def set_indeterminate(self, on: bool) -> None:
        self.progress.setRange(0, 0) if on else self.progress.setRange(0, 100)


class LogView(QTextEdit):
    """Collapsible activity log with severity colouring."""

    COLORS = {"info": "", "success": "#0F9D58", "warning": "#C77700", "error": "#D23F31"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setObjectName("Mono")
        self.setMinimumHeight(96)
        self.setMaximumHeight(220)
        self.setLineWrapMode(QTextEdit.WidgetWidth)

    def append_line(self, text: str, level: str = "info") -> None:
        color = self.COLORS.get(level, "")
        safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        if color:
            self.append(f'<span style="color:{color}">{safe}</span>')
        else:
            self.append(safe)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def clear_log(self) -> None:
        self.clear()


class ResultPanel(Card):
    """Post-run summary with 'open' and 'show in folder' actions."""

    open_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__("Result", parent=parent)
        self.setVisible(False)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.body.addWidget(self.summary)

        self.detail = QLabel("")
        self.detail.setObjectName("Subtle")
        self.detail.setWordWrap(True)
        self.body.addWidget(self.detail)

        self.files_box = QVBoxLayout()
        self.files_box.setSpacing(4)
        self.body.addLayout(self.files_box)

        row = QHBoxLayout()
        self.open_btn = QPushButton("Open file")
        self.open_btn.setObjectName("Primary")
        self.folder_btn = QPushButton("Show in folder")
        self.folder_btn.setObjectName("Ghost")
        row.addWidget(self.open_btn)
        row.addWidget(self.folder_btn)
        row.addStretch(1)
        self.body.addLayout(row)

        self._paths: list[Path] = []
        self.open_btn.clicked.connect(self._open_first)
        self.folder_btn.clicked.connect(self._reveal_first)

    def show_result(self, summary: str, paths: Sequence[Path] | None = None,
                    detail: str = "") -> None:
        self.summary.setText(summary)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self._paths = [Path(p) for p in (paths or [])]

        while self.files_box.count():
            item = self.files_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for p in self._paths[:8]:
            row = QHBoxLayout()
            name = QLabel(f"📄  {p.name}")
            name.setToolTip(str(p))
            size = QLabel(utils.human_size(utils.file_size(p)))
            size.setObjectName("Subtle")
            row.addWidget(name, 1)
            row.addWidget(size)
            holder = QWidget()
            holder.setLayout(row)
            self.files_box.addWidget(holder)

        if len(self._paths) > 8:
            more = QLabel(f"…and {len(self._paths) - 8} more")
            more.setObjectName("Subtle")
            self.files_box.addWidget(more)

        has = bool(self._paths)
        self.open_btn.setVisible(has)
        self.folder_btn.setVisible(has)
        self.open_btn.setText("Open folder" if has and self._paths[0].is_dir() else "Open file")
        self.setVisible(True)

    def _open_first(self) -> None:
        if self._paths:
            utils.open_path(self._paths[0])

    def _reveal_first(self) -> None:
        if self._paths:
            utils.reveal_in_explorer(self._paths[0])


# ---------------------------------------------------------------------------
# scrollable page container
# ---------------------------------------------------------------------------

class ScrollArea(QScrollArea):
    """Vertical scroll host with a transparent background and sane defaults."""

    def __init__(self, parent=None, margins: tuple[int, int, int, int] = (28, 24, 28, 28),
                 spacing: int = 16):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.inner = QWidget()
        self.inner.setAttribute(Qt.WA_StyledBackground, False)
        self.layout_ = QVBoxLayout(self.inner)
        self.layout_.setContentsMargins(*margins)
        self.layout_.setSpacing(spacing)
        self.setWidget(self.inner)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.layout_.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout) -> None:
        self.layout_.addLayout(layout)

    def add_stretch(self, value: int = 1) -> None:
        self.layout_.addStretch(value)


def columns(*widgets: QWidget, spacing: int = 16, stretches: Sequence[int] | None = None) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    for i, w in enumerate(widgets):
        lay.addWidget(w, stretches[i] if stretches and i < len(stretches) else 1)
    return lay


def copy_to_clipboard(text: str) -> None:
    QApplication.clipboard().setText(text)


def toast(parent: QWidget, message: str, kind: str = "success", msecs: int = 2600) -> None:
    """Transient confirmation that slides in at the bottom of the window."""
    t = _Toast(parent, message, kind)
    t.show_for(msecs)


class _Toast(QFrame):
    def __init__(self, parent: QWidget, message: str, kind: str):
        super().__init__(parent)
        self.setObjectName({"success": "SuccessBanner", "error": "DangerBanner",
                            "warning": "WarnBanner"}.get(kind, "InfoBanner"))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 11, 16, 11)
        icon = QLabel({"success": "✓", "error": "⛔", "warning": "⚠"}.get(kind, "ⓘ"))
        lay.addWidget(icon)
        lb = QLabel(message)
        lb.setWordWrap(True)
        lay.addWidget(lb)
        elevate(self, blur=26, alpha=48, dy=4)
        self.adjustSize()

    def show_for(self, msecs: int) -> None:
        p = self.parentWidget()
        if p is None:
            return
        self.adjustSize()
        w = min(max(self.sizeHint().width(), 240), p.width() - 60)
        self.setFixedWidth(w)
        x = (p.width() - w) // 2
        end_y = p.height() - self.sizeHint().height() - 28
        self.move(x, end_y + 24)
        self.show()
        self.raise_()

        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(200)
        anim.setStartValue(QPoint(x, end_y + 24))
        anim.setEndValue(QPoint(x, end_y))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._anim = anim
        QTimer.singleShot(msecs, self._dismiss)

    def _dismiss(self) -> None:
        try:
            self.hide()
            self.deleteLater()
        except RuntimeError:
            pass
