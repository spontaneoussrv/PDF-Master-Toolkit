"""
Visual page organiser.

A thumbnail grid backed by an explicit *plan* — an ordered list of
:class:`~app.core.pdf_ops.PageSlot` entries. Reorder, delete, duplicate,
rotate and insert all mutate the plan, never the file, so nothing is written
until the user saves and undo/redo is simply a stack of plan snapshots.

Thumbnails render lazily in the background, a few at a time, so a 900-page
document opens instantly.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import (QMutex, QMutexLocker, QObject, QSize, Qt, QTimer,
                            Signal)
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QListView, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)

from app.core import utils
from app.core.pdf_ops import PageSlot
from app.core.pdfbase import open_pdf, pymupdf

THUMB_W = 132
THUMB_H = 176


@dataclass
class Slot:
    """A plan entry plus the display state the grid needs."""
    source: int | None = None
    rotation: int = 0
    kind: str = "page"                 # page | blank | pdf | image
    ref: Path | None = None
    ref_index: int = 0
    label: str = ""

    def to_page_slot(self, blank_size: tuple[float, float]) -> PageSlot:
        if self.kind == "blank":
            return PageSlot(source=None, rotation=self.rotation, blank_size=blank_size)
        if self.kind == "pdf":
            return PageSlot(source=None, rotation=self.rotation,
                            insert_path=self.ref, insert_index=self.ref_index)
        if self.kind == "image":
            return PageSlot(source=None, rotation=self.rotation,
                            image_path=self.ref, blank_size=blank_size)
        return PageSlot(source=self.source, rotation=self.rotation)


class _ThumbSource(QObject):
    """Mutex-guarded document used only for thumbnail rendering."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = None
        self._extra: dict[str, object] = {}
        self._mutex = QMutex()

    def open(self, path: Path, password: str | None = None) -> None:
        self.close()
        with QMutexLocker(self._mutex):
            self._doc = open_pdf(path, password)

    def close(self) -> None:
        with QMutexLocker(self._mutex):
            for d in list(self._extra.values()):
                try:
                    d.close()                       # type: ignore[union-attr]
                except Exception:
                    pass
            self._extra.clear()
            if self._doc is not None:
                try:
                    self._doc.close()
                except Exception:
                    pass
            self._doc = None

    @property
    def page_count(self) -> int:
        with QMutexLocker(self._mutex):
            return self._doc.page_count if self._doc else 0

    def page_size(self, index: int) -> tuple[float, float]:
        with QMutexLocker(self._mutex):
            if self._doc and 0 <= index < self._doc.page_count:
                r = self._doc[index].rect
                return (r.width, r.height)
        return (595.0, 842.0)

    def thumbnail(self, slot: Slot) -> QPixmap | None:
        try:
            if slot.kind == "blank":
                return self._blank_pixmap()
            if slot.kind == "image" and slot.ref:
                pm = QPixmap(str(slot.ref))
                if pm.isNull():
                    return None
                return self._fit(pm, slot.rotation)
            if slot.kind == "pdf" and slot.ref:
                with QMutexLocker(self._mutex):
                    key = str(slot.ref)
                    doc = self._extra.get(key)
                    if doc is None:
                        doc = pymupdf.open(key)
                        self._extra[key] = doc
                    idx = utils.clamp(slot.ref_index, 0, doc.page_count - 1)   # type: ignore[union-attr]
                    img = self._render(doc, idx)
                return self._fit(QPixmap.fromImage(img), slot.rotation) if img else None

            with QMutexLocker(self._mutex):
                if self._doc is None or slot.source is None:
                    return None
                if not (0 <= slot.source < self._doc.page_count):
                    return None
                img = self._render(self._doc, slot.source)
            return self._fit(QPixmap.fromImage(img), slot.rotation) if img else None
        except Exception:
            return None

    def _render(self, doc, index: int) -> QImage | None:
        try:
            r = doc[index].rect
            zoom = min((THUMB_W - 8) / max(1.0, r.width), (THUMB_H - 26) / max(1.0, r.height))
            pix = doc[index].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        except Exception:
            return None
        return QImage(pix.samples, pix.width, pix.height, pix.stride,
                      QImage.Format_RGB888).copy()

    def _fit(self, pm: QPixmap, rotation: int) -> QPixmap:
        if rotation:
            from PySide6.QtGui import QTransform
            pm = pm.transformed(QTransform().rotate(rotation), Qt.SmoothTransformation)
        return pm.scaled(THUMB_W - 8, THUMB_H - 26, Qt.KeepAspectRatio,
                         Qt.SmoothTransformation)

    def _blank_pixmap(self) -> QPixmap:
        pm = QPixmap(THUMB_W - 8, THUMB_H - 26)
        pm.fill(QColor("#FFFFFF"))
        p = QPainter(pm)
        p.setPen(QPen(QColor("#C9D2E0"), 1, Qt.DashLine))
        p.drawRect(0, 0, pm.width() - 1, pm.height() - 1)
        p.setPen(QColor("#9AA6BC"))
        p.drawText(pm.rect(), Qt.AlignCenter, "Blank")
        p.end()
        return pm


class PageOrganizer(QWidget):
    """The page grid plus its toolbar."""

    plan_changed = Signal()
    page_activated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source: Path | None = None
        self.password: str | None = None
        self.slots: list[Slot] = []
        self._undo: list[list[Slot]] = []
        self._redo: list[list[Slot]] = []
        self._thumbs = _ThumbSource(self)
        self._cursor = 0
        self._blank_size = (595.0, 842.0)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.addWidget(self._build_toolbar())

        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setIconSize(QSize(THUMB_W - 8, THUMB_H - 26))
        self.grid.setGridSize(QSize(THUMB_W + 10, THUMB_H + 12))
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Snap)
        self.grid.setSpacing(6)
        self.grid.setUniformItemSizes(True)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setDragDropMode(QAbstractItemView.InternalMove)
        self.grid.setDefaultDropAction(Qt.MoveAction)
        self.grid.setWordWrap(True)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._menu)
        self.grid.model().rowsMoved.connect(self._on_rows_moved)
        self.grid.itemDoubleClicked.connect(self._on_activate)
        self.grid.itemSelectionChanged.connect(self._update_buttons)
        root.addWidget(self.grid, 1)

        self.status = QLabel("No document loaded")
        self.status.setObjectName("Subtle")
        root.addWidget(self.status)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._render_batch)
        self._update_buttons()

    # -- toolbar -----------------------------------------------------------
    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("ViewerToolbar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(4)

        def tb(text, tip, slot):
            b = QPushButton(text)
            b.setObjectName("ToolbarButton")
            b.setToolTip(tip)
            b.clicked.connect(slot)
            lay.addWidget(b)
            return b

        self.undo_btn = tb("↶ Undo", "Undo the last change", self.undo)
        self.redo_btn = tb("↷ Redo", "Redo", self.redo)
        lay.addWidget(self._sep())
        self.rotl_btn = tb("↺ Rotate left", "Rotate the selected pages 90° left",
                           lambda: self.rotate_selected(-90))
        self.rotr_btn = tb("↻ Rotate right", "Rotate the selected pages 90° right",
                           lambda: self.rotate_selected(90))
        lay.addWidget(self._sep())
        self.dup_btn = tb("⧉ Duplicate", "Duplicate the selected pages", self.duplicate_selected)
        self.del_btn = tb("⌦ Delete", "Remove the selected pages", self.delete_selected)
        lay.addWidget(self._sep())
        self.blank_btn = tb("＋ Blank page", "Insert a blank page after the selection",
                            self.insert_blank)
        self.pdf_btn = tb("＋ PDF", "Insert pages from another PDF", self.insert_pdf)
        self.img_btn = tb("＋ Image", "Insert an image as a new page", self.insert_image)
        lay.addStretch(1)
        self.select_all_btn = tb("Select all", "Select every page", self.grid_select_all)
        self.reset_btn = tb("Reset", "Discard all changes", self.reset)
        return bar

    def _sep(self) -> QFrame:
        f = QFrame()
        f.setObjectName("VSeparator")
        f.setFixedWidth(1)
        f.setFixedHeight(20)
        return f

    # -- loading -----------------------------------------------------------
    def load(self, path: str | Path, password: str | None = None) -> bool:
        try:
            self._thumbs.open(Path(path), password)
        except Exception as e:
            QMessageBox.warning(self, "Could not open", str(e))
            return False
        self.source = Path(path)
        self.password = password
        n = self._thumbs.page_count
        self._blank_size = self._thumbs.page_size(0) if n else (595.0, 842.0)
        self.slots = [Slot(source=i) for i in range(n)]
        self._undo.clear()
        self._redo.clear()
        self._rebuild()
        return True

    def reset(self) -> None:
        if not self.source:
            return
        if self._undo or self._redo:
            self._push_undo()
        n = self._thumbs.page_count
        self.slots = [Slot(source=i) for i in range(n)]
        self._rebuild()

    def close_document(self) -> None:
        self._thumbs.close()
        self.slots.clear()
        self.grid.clear()
        self.source = None
        self._update_status()

    # -- grid ---------------------------------------------------------------
    def _rebuild(self) -> None:
        self.grid.blockSignals(True)
        self.grid.clear()
        for i, slot in enumerate(self.slots):
            item = QListWidgetItem(self._caption(i, slot))
            item.setSizeHint(QSize(THUMB_W, THUMB_H))
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignBottom)
            item.setData(Qt.UserRole, i)
            self.grid.addItem(item)
        self.grid.blockSignals(False)
        self._cursor = 0
        self._timer.start()
        self._update_status()
        self._update_buttons()
        self.plan_changed.emit()

    def _caption(self, index: int, slot: Slot) -> str:
        if slot.kind == "blank":
            base = "Blank"
        elif slot.kind == "image":
            base = utils.elide(slot.ref.name if slot.ref else "Image", 14)
        elif slot.kind == "pdf":
            base = f"{utils.elide(slot.ref.stem if slot.ref else 'PDF', 10)} p{slot.ref_index + 1}"
        else:
            base = f"Page {(slot.source or 0) + 1}"
        suffix = f"  {slot.rotation}°" if slot.rotation else ""
        return f"{index + 1}. {base}{suffix}"

    def _render_batch(self) -> None:
        end = min(self._cursor + 6, len(self.slots))
        for i in range(self._cursor, end):
            item = self.grid.item(i)
            if item is None:
                continue
            pm = self._thumbs.thumbnail(self.slots[i])
            if pm is not None:
                item.setIcon(pm)
        self._cursor = end
        if end < len(self.slots):
            self._timer.start()

    def _refresh_captions(self) -> None:
        for i, slot in enumerate(self.slots):
            item = self.grid.item(i)
            if item is not None:
                item.setText(self._caption(i, slot))

    def _refresh_icons(self, indices: Sequence[int]) -> None:
        for i in indices:
            if 0 <= i < len(self.slots):
                item = self.grid.item(i)
                if item is not None:
                    pm = self._thumbs.thumbnail(self.slots[i])
                    if pm is not None:
                        item.setIcon(pm)

    def _on_rows_moved(self, *_args) -> None:
        """The view reordered itself; mirror that into the plan."""
        order = []
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            order.append(int(item.data(Qt.UserRole)))
        if sorted(order) != list(range(len(self.slots))):
            return
        self._push_undo()
        self.slots = [self.slots[i] for i in order]
        for row in range(self.grid.count()):
            self.grid.item(row).setData(Qt.UserRole, row)
        self._refresh_captions()
        self._update_status()
        self.plan_changed.emit()

    def _on_activate(self, item: QListWidgetItem) -> None:
        row = self.grid.row(item)
        slot = self.slots[row] if 0 <= row < len(self.slots) else None
        if slot and slot.kind == "page" and slot.source is not None:
            self.page_activated.emit(slot.source)

    # -- editing -----------------------------------------------------------
    def selected_rows(self) -> list[int]:
        return sorted(self.grid.row(i) for i in self.grid.selectedItems())

    def _push_undo(self) -> None:
        self._undo.append(copy.deepcopy(self.slots))
        if len(self._undo) > 60:
            self._undo.pop(0)
        self._redo.clear()
        self._update_buttons()

    def undo(self) -> None:
        if not self._undo:
            return
        self._redo.append(copy.deepcopy(self.slots))
        self.slots = self._undo.pop()
        self._rebuild()

    def redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(copy.deepcopy(self.slots))
        self.slots = self._redo.pop()
        self._rebuild()

    def rotate_selected(self, angle: int) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        self._push_undo()
        for r in rows:
            self.slots[r].rotation = (self.slots[r].rotation + angle) % 360
        self._refresh_captions()
        self._refresh_icons(rows)
        self.plan_changed.emit()

    def duplicate_selected(self) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        self._push_undo()
        for offset, r in enumerate(rows):
            self.slots.insert(r + offset + 1, copy.deepcopy(self.slots[r + offset]))
        self._rebuild()

    def delete_selected(self) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        if len(rows) >= len(self.slots):
            QMessageBox.information(self, "Cannot delete every page",
                                    "A document must keep at least one page.")
            return
        self._push_undo()
        for r in reversed(rows):
            del self.slots[r]
        self._rebuild()

    def insert_blank(self) -> None:
        rows = self.selected_rows()
        at = (rows[-1] + 1) if rows else len(self.slots)
        self._push_undo()
        self.slots.insert(at, Slot(kind="blank"))
        self._rebuild()

    def insert_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Insert pages from a PDF", "",
                                              "PDF files (*.pdf)")
        if not path:
            return
        try:
            other = pymupdf.open(path)
            count = other.page_count
            other.close()
        except Exception as e:
            QMessageBox.warning(self, "Could not open", str(e))
            return
        if count == 0:
            return
        rows = self.selected_rows()
        at = (rows[-1] + 1) if rows else len(self.slots)
        self._push_undo()
        for i in range(count):
            self.slots.insert(at + i, Slot(kind="pdf", ref=Path(path), ref_index=i))
        self._rebuild()

    def insert_image(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Insert images as pages", "",
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp)")
        if not files:
            return
        rows = self.selected_rows()
        at = (rows[-1] + 1) if rows else len(self.slots)
        self._push_undo()
        for i, f in enumerate(files):
            self.slots.insert(at + i, Slot(kind="image", ref=Path(f)))
        self._rebuild()

    def move_selected(self, delta: int) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        self._push_undo()
        if delta < 0:
            for r in rows:
                if r + delta >= 0:
                    self.slots.insert(r + delta, self.slots.pop(r))
        else:
            for r in reversed(rows):
                if r + delta < len(self.slots):
                    self.slots.insert(r + delta, self.slots.pop(r))
        self._rebuild()

    def reverse_all(self) -> None:
        self._push_undo()
        self.slots.reverse()
        self._rebuild()

    def grid_select_all(self) -> None:
        self.grid.selectAll()

    def select_odd(self) -> None:
        self.grid.clearSelection()
        for i in range(0, self.grid.count(), 2):
            self.grid.item(i).setSelected(True)

    def select_even(self) -> None:
        self.grid.clearSelection()
        for i in range(1, self.grid.count(), 2):
            self.grid.item(i).setSelected(True)

    # -- menu / state ------------------------------------------------------
    def _menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Rotate right", lambda: self.rotate_selected(90))
        menu.addAction("Rotate left", lambda: self.rotate_selected(-90))
        menu.addAction("Rotate 180°", lambda: self.rotate_selected(180))
        menu.addSeparator()
        menu.addAction("Duplicate", self.duplicate_selected)
        menu.addAction("Delete", self.delete_selected)
        menu.addSeparator()
        menu.addAction("Move to start", lambda: self.move_selected(-len(self.slots)))
        menu.addAction("Move to end", lambda: self.move_selected(len(self.slots)))
        menu.addSeparator()
        menu.addAction("Insert blank page", self.insert_blank)
        menu.addAction("Insert PDF…", self.insert_pdf)
        menu.addAction("Insert image…", self.insert_image)
        menu.addSeparator()
        menu.addAction("Select all", self.grid_select_all)
        menu.addAction("Select odd pages", self.select_odd)
        menu.addAction("Select even pages", self.select_even)
        menu.addAction("Reverse order", self.reverse_all)
        menu.exec(self.grid.mapToGlobal(pos))

    def _update_buttons(self) -> None:
        has_doc = bool(self.slots)
        has_sel = bool(self.grid.selectedItems())
        for b in (self.rotl_btn, self.rotr_btn, self.dup_btn, self.del_btn):
            b.setEnabled(has_sel)
        for b in (self.blank_btn, self.pdf_btn, self.img_btn,
                  self.select_all_btn, self.reset_btn):
            b.setEnabled(has_doc)
        self.undo_btn.setEnabled(bool(self._undo))
        self.redo_btn.setEnabled(bool(self._redo))

    def _update_status(self) -> None:
        if not self.slots:
            self.status.setText("No document loaded")
            return
        original = self._thumbs.page_count
        changed = self.has_changes()
        bits = [f"{len(self.slots)} page(s)"]
        if original and len(self.slots) != original:
            bits.append(f"was {original}")
        rot = sum(1 for s in self.slots if s.rotation)
        if rot:
            bits.append(f"{rot} rotated")
        ins = sum(1 for s in self.slots if s.kind != "page")
        if ins:
            bits.append(f"{ins} inserted")
        bits.append("modified" if changed else "unchanged")
        self.status.setText("  ·  ".join(bits))

    def has_changes(self) -> bool:
        n = self._thumbs.page_count
        if len(self.slots) != n:
            return True
        for i, s in enumerate(self.slots):
            if s.kind != "page" or s.source != i or s.rotation:
                return True
        return False

    # -- output ------------------------------------------------------------
    def build_plan(self) -> list[PageSlot]:
        return [s.to_page_slot(self._blank_size) for s in self.slots]

    @property
    def page_count(self) -> int:
        return len(self.slots)
