"""
Integrated PDF viewer with lazy page rendering.

Only the pages currently on screen (plus a small look-ahead buffer) are ever
rasterised, and finished bitmaps live in a bounded LRU cache. A 2,000-page
document therefore costs the same to open as a 5-page one — the page column is
built from lightweight placeholders sized from each page's geometry, and pixels
arrive as you scroll.

Rendering happens on a thread pool. PyMuPDF documents are not safe to use from
several threads at once, so all access is serialised through one mutex; the
render itself is short, so this never becomes the bottleneck.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import (QMutex, QMutexLocker, QObject, QPoint, QRect,
                            QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer,
                            Signal)
from PySide6.QtGui import (QColor, QCursor, QGuiApplication, QImage, QPainter,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QPushButton, QScrollArea, QSizePolicy,
                               QSplitter, QVBoxLayout, QWidget)

from app import config
from app.core import utils
from app.core.pdfbase import PdfLocked, open_pdf, pymupdf

ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.25, 1.5, 1.75, 2.0,
              2.5, 3.0, 4.0, 6.0, 8.0]
MAX_CACHE_PAGES = 40
BUFFER_PAGES = 2


# ---------------------------------------------------------------------------
# document handle
# ---------------------------------------------------------------------------

class DocumentHandle(QObject):
    """A shared, mutex-guarded PyMuPDF document."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = None
        self._mutex = QMutex()
        self.path: Path | None = None
        self.password: str | None = None

    def open(self, path: str | Path, password: str | None = None) -> None:
        self.close()
        with QMutexLocker(self._mutex):
            self._doc = open_pdf(path, password)
            self.path = Path(path)
            self.password = password

    def close(self) -> None:
        with QMutexLocker(self._mutex):
            if self._doc is not None:
                try:
                    self._doc.close()
                except Exception:
                    pass
            self._doc = None
            self.path = None

    @property
    def is_open(self) -> bool:
        return self._doc is not None

    @property
    def page_count(self) -> int:
        with QMutexLocker(self._mutex):
            return self._doc.page_count if self._doc else 0

    def page_sizes(self) -> list[tuple[float, float, int]]:
        """(width, height, rotation) for every page, in points."""
        out: list[tuple[float, float, int]] = []
        with QMutexLocker(self._mutex):
            if self._doc is None:
                return out
            for i in range(self._doc.page_count):
                try:
                    r = self._doc[i].rect
                    out.append((r.width, r.height, self._doc[i].rotation))
                except Exception:
                    out.append((595.0, 842.0, 0))
        return out

    def render(self, index: int, zoom: float, rotation: int = 0) -> QImage | None:
        with QMutexLocker(self._mutex):
            if self._doc is None or not (0 <= index < self._doc.page_count):
                return None
            try:
                matrix = pymupdf.Matrix(zoom, zoom)
                if rotation:
                    matrix = matrix * pymupdf.Matrix(rotation)
                pix = self._doc[index].get_pixmap(matrix=matrix, alpha=False)
            except Exception:
                return None
        img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                     QImage.Format_RGB888)
        return img.copy()          # detach from the pixmap buffer before it dies

    def page_text(self, index: int) -> str:
        with QMutexLocker(self._mutex):
            if self._doc is None or not (0 <= index < self._doc.page_count):
                return ""
            try:
                return self._doc[index].get_text("text") or ""
            except Exception:
                return ""

    def textbox(self, index: int, rect: tuple[float, float, float, float]) -> str:
        with QMutexLocker(self._mutex):
            if self._doc is None or not (0 <= index < self._doc.page_count):
                return ""
            try:
                return self._doc[index].get_textbox(pymupdf.Rect(*rect)) or ""
            except Exception:
                return ""

    def search(self, index: int, term: str) -> list[tuple[float, float, float, float]]:
        with QMutexLocker(self._mutex):
            if self._doc is None or not (0 <= index < self._doc.page_count):
                return []
            try:
                flags = getattr(pymupdf, "TEXT_IGNORECASE", 0)
                hits = self._doc[index].search_for(term, flags=flags) if flags \
                    else self._doc[index].search_for(term)
                return [(r.x0, r.y0, r.x1, r.y1) for r in hits]
            except Exception:
                return []

    def toc(self) -> list:
        with QMutexLocker(self._mutex):
            if self._doc is None:
                return []
            try:
                return self._doc.get_toc(simple=True) or []
            except Exception:
                return []


# ---------------------------------------------------------------------------
# render worker
# ---------------------------------------------------------------------------

class _RenderSignals(QObject):
    done = Signal(int, float, int, object)      # index, zoom, generation, QImage


class _RenderTask(QRunnable):
    def __init__(self, handle: DocumentHandle, index: int, zoom: float,
                 rotation: int, generation: int, signals: _RenderSignals):
        super().__init__()
        self.handle = handle
        self.index = index
        self.zoom = zoom
        self.rotation = rotation
        self.generation = generation
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        img = self.handle.render(self.index, self.zoom, self.rotation)
        if img is not None:
            self.signals.done.emit(self.index, self.zoom, self.generation, img)


# ---------------------------------------------------------------------------
# page widget
# ---------------------------------------------------------------------------

class PageWidget(QFrame):
    """One page slot. Draws a placeholder until its bitmap arrives."""

    clicked = Signal(int, QPoint)
    selection_made = Signal(int, tuple)

    def __init__(self, index: int, size_pt: tuple[float, float], parent=None):
        super().__init__(parent)
        self.index = index
        self.width_pt, self.height_pt = size_pt
        self.pixmap: QPixmap | None = None
        self.highlights: list[tuple[float, float, float, float]] = []
        self.current_highlight: int = -1
        self.zoom = 1.0
        self.selecting = False
        self.sel_start = QPoint()
        self.sel_end = QPoint()
        self.selectable = False

        self.setObjectName("PageCanvas")
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setMouseTracking(True)

    def set_zoom(self, zoom: float) -> None:
        self.zoom = zoom
        w = max(1, int(self.width_pt * zoom))
        h = max(1, int(self.height_pt * zoom))
        self.setFixedSize(w, h)
        self.pixmap = None
        self.update()

    def set_image(self, image: QImage) -> None:
        self.pixmap = QPixmap.fromImage(image)
        self.update()

    def clear_image(self) -> None:
        self.pixmap = None
        self.update()

    def set_highlights(self, rects: Sequence[tuple], current: int = -1) -> None:
        self.highlights = list(rects)
        self.current_highlight = current
        self.update()

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = self.rect()

        if self.pixmap is not None:
            painter.drawPixmap(rect, self.pixmap)
        else:
            painter.fillRect(rect, QColor("#FFFFFF"))
            painter.setPen(QPen(QColor("#D8DEE9"), 1))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
            painter.setPen(QColor("#B4BECC"))
            painter.drawText(rect, Qt.AlignCenter, f"Page {self.index + 1}")

        if self.highlights:
            for i, (x0, y0, x1, y1) in enumerate(self.highlights):
                r = QRectF(x0 * self.zoom, y0 * self.zoom,
                           (x1 - x0) * self.zoom, (y1 - y0) * self.zoom)
                if i == self.current_highlight:
                    painter.fillRect(r, QColor(255, 170, 20, 130))
                    painter.setPen(QPen(QColor(220, 130, 0), 1.4))
                    painter.drawRect(r)
                else:
                    painter.fillRect(r, QColor(255, 230, 120, 110))

        if self.selecting:
            band = QRect(self.sel_start, self.sel_end).normalized()
            painter.fillRect(band, QColor(47, 107, 255, 46))
            painter.setPen(QPen(QColor(47, 107, 255), 1))
            painter.drawRect(band)

        painter.end()

    # -- mouse -------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.selectable:
            self.selecting = True
            self.sel_start = event.position().toPoint()
            self.sel_end = self.sel_start
            self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.selecting:
            self.sel_end = event.position().toPoint()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.selecting and event.button() == Qt.LeftButton:
            self.selecting = False
            band = QRect(self.sel_start, self.sel_end).normalized()
            self.update()
            if band.width() > 4 and band.height() > 4:
                z = max(0.01, self.zoom)
                self.selection_made.emit(self.index, (
                    band.left() / z, band.top() / z,
                    band.right() / z, band.bottom() / z))
        elif event.button() == Qt.LeftButton:
            self.clicked.emit(self.index, event.position().toPoint())
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# viewer
# ---------------------------------------------------------------------------

@dataclass
class SearchState:
    term: str = ""
    hits: list[tuple[int, tuple]] = None            # (page index, rect)
    current: int = -1

    def __post_init__(self):
        if self.hits is None:
            self.hits = []


class PdfViewer(QWidget):
    """Continuous, lazily-rendered page view with a thumbnail rail."""

    page_changed = Signal(int)
    document_loaded = Signal(int)
    text_copied = Signal(str)
    error = Signal(str)

    def __init__(self, parent=None, show_thumbnails: bool = True,
                 show_toolbar: bool = True):
        super().__init__(parent)
        self.handle = DocumentHandle(self)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(2, min(4, (QThreadPool.globalInstance().maxThreadCount() or 4))))
        self.signals = _RenderSignals()
        self.signals.done.connect(self._on_rendered)

        self._pages: list[PageWidget] = []
        self._sizes: list[tuple[float, float, int]] = []
        self._cache: OrderedDict[tuple[int, float], QImage] = OrderedDict()
        self._requested: set[tuple[int, float]] = set()
        self._generation = 0
        self._zoom = 1.0
        self._fit_mode = "width"                 # width | page | none
        self._rotation = 0
        self._current = 0
        self._search = SearchState()
        self._pan = False
        self._pan_origin = QPoint()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        if show_toolbar:
            root.addWidget(self._build_toolbar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(True)

        self.thumbs = QListWidget()
        self.thumbs.setObjectName("ThumbStrip")
        self.thumbs.setIconSize(QSize(96, 128))
        self.thumbs.setSpacing(4)
        self.thumbs.setUniformItemSizes(True)
        self.thumbs.setVisible(show_thumbnails)
        self.thumbs.setMaximumWidth(168)
        self.thumbs.setMinimumWidth(128)
        self.thumbs.currentRowChanged.connect(self._on_thumb_selected)
        splitter.addWidget(self.thumbs)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("ViewerCanvas")
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.canvas = QWidget()
        self.canvas_layout = QVBoxLayout(self.canvas)
        self.canvas_layout.setContentsMargins(20, 20, 20, 20)
        self.canvas_layout.setSpacing(14)
        self.canvas_layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.scroll.setWidget(self.canvas)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.scroll.setContextMenuPolicy(Qt.CustomContextMenu)
        self.scroll.customContextMenuRequested.connect(self._context_menu)
        splitter.addWidget(self.scroll)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([150, 900])
        self.splitter = splitter
        root.addWidget(splitter, 1)

        self.empty_label = QLabel("No document open")
        self.empty_label.setObjectName("Subtle")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.canvas_layout.addWidget(self.empty_label)

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(60)
        self._scroll_timer.timeout.connect(self._update_visible)

        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(120)
        self._thumb_timer.timeout.connect(self._render_thumbs)
        self._thumb_cursor = 0

    # -- toolbar -----------------------------------------------------------
    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("ViewerToolbar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(4)

        def tb(text: str, tip: str, slot, checkable: bool = False) -> QPushButton:
            b = QPushButton(text)
            b.setObjectName("ToolbarButton")
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.clicked.connect(slot)
            lay.addWidget(b)
            return b

        self.thumb_toggle = tb("▤", "Show or hide page thumbnails",
                               self._toggle_thumbs, checkable=True)
        self.thumb_toggle.setChecked(True)
        lay.addWidget(self._sep())

        tb("◀", "Previous page", self.previous_page)
        self.page_box = QLineEdit("0")
        self.page_box.setFixedWidth(52)
        self.page_box.setAlignment(Qt.AlignCenter)
        self.page_box.returnPressed.connect(self._goto_typed)
        lay.addWidget(self.page_box)
        self.page_total = QLabel("/ 0")
        self.page_total.setObjectName("Subtle")
        lay.addWidget(self.page_total)
        tb("▶", "Next page", self.next_page)
        lay.addWidget(self._sep())

        tb("−", "Zoom out", self.zoom_out)
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(False)
        self.zoom_box.setFixedWidth(112)
        self.zoom_box.addItem("Fit width", "width")
        self.zoom_box.addItem("Fit page", "page")
        for z in ZOOM_STEPS:
            self.zoom_box.addItem(f"{int(z * 100)}%", z)
        self.zoom_box.currentIndexChanged.connect(self._on_zoom_choice)
        lay.addWidget(self.zoom_box)
        tb("＋", "Zoom in", self.zoom_in)
        lay.addWidget(self._sep())

        tb("↻", "Rotate the view (does not change the file)", self.rotate_view)
        self.pan_btn = tb("✋", "Hand tool — drag to pan", self._toggle_pan, checkable=True)
        self.select_btn = tb("⌶", "Select text by dragging a box",
                             self._toggle_select, checkable=True)
        lay.addWidget(self._sep())

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Find in document…")
        self.search_box.setMinimumWidth(180)
        self.search_box.returnPressed.connect(self.find_next)
        lay.addWidget(self.search_box, 1)
        tb("◂", "Previous match", self.find_previous)
        tb("▸", "Next match", self.find_next)
        self.match_label = QLabel("")
        self.match_label.setObjectName("Subtle")
        lay.addWidget(self.match_label)

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
            self.handle.open(path, password)
        except PdfLocked as e:
            self.error.emit(str(e))
            return False
        except Exception as e:
            self.error.emit(f"Could not open this document: {e}")
            return False

        self._generation += 1
        self._cache.clear()
        self._requested.clear()
        self._search = SearchState()
        self._sizes = self.handle.page_sizes()
        self._build_pages()
        self._current = 0
        self.page_total.setText(f"/ {len(self._sizes)}")
        self.page_box.setText("1")
        self._populate_thumbs()
        QTimer.singleShot(0, self._apply_fit)
        self.document_loaded.emit(len(self._sizes))
        return True

    def close_document(self) -> None:
        self._generation += 1
        self.handle.close()
        self._clear_pages()
        self.thumbs.clear()
        self._cache.clear()
        self._requested.clear()
        self.page_total.setText("/ 0")
        self.page_box.setText("0")
        self.empty_label.setVisible(True)

    def _clear_pages(self) -> None:
        for w in self._pages:
            w.setParent(None)
            w.deleteLater()
        self._pages.clear()

    def _build_pages(self) -> None:
        self._clear_pages()
        self.empty_label.setVisible(False)
        for i, (w, h, _rot) in enumerate(self._sizes):
            page = PageWidget(i, (w, h), self.canvas)
            page.set_zoom(self._zoom)
            page.selection_made.connect(self._on_selection)
            self.canvas_layout.insertWidget(self.canvas_layout.count() - 1, page,
                                            0, Qt.AlignHCenter)
            self._pages.append(page)

    # -- zoom / fit --------------------------------------------------------
    def _apply_fit(self) -> None:
        if not self._sizes:
            return
        viewport = self.scroll.viewport().width() - 56
        if self._fit_mode == "width":
            widest = max(s[0] for s in self._sizes)
            self._set_zoom(max(0.1, viewport / max(1.0, widest)), keep_mode=True)
        elif self._fit_mode == "page":
            vh = self.scroll.viewport().height() - 56
            w, h = self._sizes[min(self._current, len(self._sizes) - 1)][:2]
            self._set_zoom(max(0.1, min(viewport / w, vh / h)), keep_mode=True)

    def _set_zoom(self, zoom: float, keep_mode: bool = False) -> None:
        zoom = float(utils.clamp(zoom, 0.08, 10.0))
        if abs(zoom - self._zoom) < 0.001:
            return
        self._zoom = zoom
        if not keep_mode:
            self._fit_mode = "none"
        self._generation += 1
        self._requested.clear()
        for p in self._pages:
            p.set_zoom(zoom)
        self._apply_search_highlights()
        self._schedule_visible()

    def zoom_in(self) -> None:
        nxt = next((z for z in ZOOM_STEPS if z > self._zoom * 1.02), ZOOM_STEPS[-1])
        self._fit_mode = "none"
        self._sync_zoom_box(nxt)
        self._set_zoom(nxt)

    def zoom_out(self) -> None:
        lower = [z for z in ZOOM_STEPS if z < self._zoom * 0.98]
        nxt = lower[-1] if lower else ZOOM_STEPS[0]
        self._fit_mode = "none"
        self._sync_zoom_box(nxt)
        self._set_zoom(nxt)

    def _sync_zoom_box(self, zoom: float) -> None:
        self.zoom_box.blockSignals(True)
        idx = self.zoom_box.findData(zoom)
        if idx >= 0:
            self.zoom_box.setCurrentIndex(idx)
        else:
            self.zoom_box.setCurrentText(f"{int(zoom * 100)}%")
        self.zoom_box.blockSignals(False)

    def _on_zoom_choice(self, _index: int) -> None:
        data = self.zoom_box.currentData()
        if data in ("width", "page"):
            self._fit_mode = data
            self._apply_fit()
        elif isinstance(data, float):
            self._fit_mode = "none"
            self._set_zoom(data)

    def fit_width(self) -> None:
        self._fit_mode = "width"
        self.zoom_box.setCurrentIndex(0)
        self._apply_fit()

    def fit_page(self) -> None:
        self._fit_mode = "page"
        self.zoom_box.setCurrentIndex(1)
        self._apply_fit()

    def rotate_view(self) -> None:
        self._rotation = (self._rotation + 90) % 360
        self._generation += 1
        self._cache.clear()
        self._requested.clear()
        # swap the placeholder aspect so layout matches the rotated render
        for i, page in enumerate(self._pages):
            w, h, _ = self._sizes[i]
            page.width_pt, page.height_pt = (h, w) if self._rotation % 180 else (w, h)
            page.set_zoom(self._zoom)
        self._schedule_visible()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_mode in ("width", "page"):
            QTimer.singleShot(40, self._apply_fit)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            self.zoom_in() if event.angleDelta().y() > 0 else self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    # -- navigation --------------------------------------------------------
    def goto_page(self, index: int) -> None:
        if not self._pages:
            return
        index = int(utils.clamp(index, 0, len(self._pages) - 1))
        self._current = index
        widget = self._pages[index]
        self.scroll.verticalScrollBar().setValue(
            widget.pos().y() - self.canvas_layout.contentsMargins().top())
        self.page_box.setText(str(index + 1))
        self._sync_thumb(index)
        self.page_changed.emit(index)

    def next_page(self) -> None:
        self.goto_page(self._current + 1)

    def previous_page(self) -> None:
        self.goto_page(self._current - 1)

    def _goto_typed(self) -> None:
        try:
            self.goto_page(int(self.page_box.text()) - 1)
        except ValueError:
            self.page_box.setText(str(self._current + 1))

    @property
    def current_page(self) -> int:
        return self._current

    @property
    def page_count(self) -> int:
        return len(self._pages)

    # -- lazy rendering ----------------------------------------------------
    def _on_scroll(self, _value: int) -> None:
        self._scroll_timer.start()

    def _schedule_visible(self) -> None:
        self._scroll_timer.start()

    def _visible_range(self) -> tuple[int, int]:
        if not self._pages:
            return (0, -1)
        top = self.scroll.verticalScrollBar().value()
        bottom = top + self.scroll.viewport().height()
        first, last = None, 0
        for i, w in enumerate(self._pages):
            y0 = w.pos().y()
            y1 = y0 + w.height()
            if y1 < top:
                continue
            if y0 > bottom:
                break
            if first is None:
                first = i
            last = i
        if first is None:
            first = last = min(self._current, len(self._pages) - 1)
        return (first, last)

    def _update_visible(self) -> None:
        if not self._pages:
            return
        first, last = self._visible_range()
        if first <= self._current <= last:
            pass
        else:
            self._current = first
            self.page_box.setText(str(first + 1))
            self._sync_thumb(first)
            self.page_changed.emit(first)

        lo = max(0, first - BUFFER_PAGES)
        hi = min(len(self._pages) - 1, last + BUFFER_PAGES)

        for i in range(lo, hi + 1):
            key = (i, round(self._zoom, 3))
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                if self._pages[i].pixmap is None:
                    self._pages[i].set_image(cached)
                continue
            if key in self._requested:
                continue
            self._requested.add(key)
            self.pool.start(_RenderTask(self.handle, i, self._zoom,
                                        self._rotation, self._generation,
                                        self.signals))

        # free bitmaps well outside the viewport so memory stays flat
        for i, page in enumerate(self._pages):
            if page.pixmap is not None and not (lo - 4 <= i <= hi + 4):
                page.clear_image()

    def _on_rendered(self, index: int, zoom: float, generation: int,
                     image: QImage) -> None:
        if generation != self._generation:
            return
        key = (index, round(zoom, 3))
        self._requested.discard(key)
        self._cache[key] = image
        self._cache.move_to_end(key)
        while len(self._cache) > MAX_CACHE_PAGES:
            self._cache.popitem(last=False)
        if 0 <= index < len(self._pages):
            self._pages[index].set_image(image)

    # -- thumbnails --------------------------------------------------------
    def _populate_thumbs(self) -> None:
        self.thumbs.clear()
        for i in range(len(self._sizes)):
            item = QListWidgetItem(f"  {i + 1}")
            item.setSizeHint(QSize(120, 148))
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignBottom)
            self.thumbs.addItem(item)
        self._thumb_cursor = 0
        self._thumb_timer.start()

    def _render_thumbs(self) -> None:
        """Fill thumbnails a handful at a time so the rail never blocks the UI."""
        if not self.thumbs.isVisible() or not self._sizes:
            return
        end = min(self._thumb_cursor + 4, len(self._sizes))
        for i in range(self._thumb_cursor, end):
            w, h, _ = self._sizes[i]
            zoom = min(96 / max(1.0, w), 128 / max(1.0, h))
            img = self.handle.render(i, zoom, self._rotation)
            if img is not None:
                item = self.thumbs.item(i)
                if item is not None:
                    item.setIcon(QPixmap.fromImage(img))
        self._thumb_cursor = end
        if end < len(self._sizes):
            self._thumb_timer.start()

    def _sync_thumb(self, index: int) -> None:
        if self.thumbs.currentRow() != index:
            self.thumbs.blockSignals(True)
            self.thumbs.setCurrentRow(index)
            self.thumbs.blockSignals(False)

    def _on_thumb_selected(self, row: int) -> None:
        if row >= 0:
            self.goto_page(row)

    def _toggle_thumbs(self) -> None:
        show = self.thumb_toggle.isChecked()
        self.thumbs.setVisible(show)
        if show and self._thumb_cursor < len(self._sizes):
            self._thumb_timer.start()

    # -- tools -------------------------------------------------------------
    def _toggle_pan(self) -> None:
        self._pan = self.pan_btn.isChecked()
        if self._pan:
            self.select_btn.setChecked(False)
            self._set_selectable(False)
        self.scroll.viewport().setCursor(
            QCursor(Qt.OpenHandCursor if self._pan else Qt.ArrowCursor))

    def _toggle_select(self) -> None:
        on = self.select_btn.isChecked()
        if on:
            self.pan_btn.setChecked(False)
            self._pan = False
            self.scroll.viewport().setCursor(QCursor(Qt.IBeamCursor))
        else:
            self.scroll.viewport().setCursor(QCursor(Qt.ArrowCursor))
        self._set_selectable(on)

    def _set_selectable(self, on: bool) -> None:
        for p in self._pages:
            p.selectable = on

    def _on_selection(self, index: int, box: tuple) -> None:
        text = self.handle.textbox(index, box).strip()
        if text:
            QApplication.clipboard().setText(text)
            self.text_copied.emit(text)

    def copy_page_text(self) -> None:
        text = self.handle.page_text(self._current)
        if text.strip():
            QApplication.clipboard().setText(text)
            self.text_copied.emit(text)

    def _context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("Copy text on this page", self.copy_page_text)
        menu.addSeparator()
        menu.addAction("Fit width", self.fit_width)
        menu.addAction("Fit page", self.fit_page)
        menu.addAction("Zoom in", self.zoom_in)
        menu.addAction("Zoom out", self.zoom_out)
        menu.addSeparator()
        menu.addAction("Rotate view", self.rotate_view)
        menu.exec(self.scroll.mapToGlobal(pos))

    # -- search ------------------------------------------------------------
    def find(self, term: str) -> int:
        term = (term or "").strip()
        self._search = SearchState(term=term)
        if not term or not self._pages:
            self._apply_search_highlights()
            self.match_label.setText("")
            return 0
        for i in range(len(self._pages)):
            for rect in self.handle.search(i, term):
                self._search.hits.append((i, rect))
        self._search.current = 0 if self._search.hits else -1
        self._apply_search_highlights()
        self._update_match_label()
        if self._search.hits:
            self.goto_page(self._search.hits[0][0])
        return len(self._search.hits)

    def find_next(self) -> None:
        term = self.search_box.text().strip()
        if term != self._search.term:
            self.find(term)
            return
        if not self._search.hits:
            return
        self._search.current = (self._search.current + 1) % len(self._search.hits)
        self._jump_to_hit()

    def find_previous(self) -> None:
        term = self.search_box.text().strip()
        if term != self._search.term:
            self.find(term)
            return
        if not self._search.hits:
            return
        self._search.current = (self._search.current - 1) % len(self._search.hits)
        self._jump_to_hit()

    def _jump_to_hit(self) -> None:
        page, _rect = self._search.hits[self._search.current]
        self._apply_search_highlights()
        self._update_match_label()
        self.goto_page(page)

    def _update_match_label(self) -> None:
        n = len(self._search.hits)
        if not self._search.term:
            self.match_label.setText("")
        elif n == 0:
            self.match_label.setText("No matches")
        else:
            self.match_label.setText(f"{self._search.current + 1} of {n}")

    def _apply_search_highlights(self) -> None:
        by_page: dict[int, list[tuple]] = {}
        for i, (page, rect) in enumerate(self._search.hits):
            by_page.setdefault(page, []).append(rect)
        current_page, current_rect = (self._search.hits[self._search.current]
                                      if 0 <= self._search.current < len(self._search.hits)
                                      else (-1, None))
        for i, widget in enumerate(self._pages):
            rects = by_page.get(i, [])
            cur = rects.index(current_rect) if (i == current_page and current_rect in rects) else -1
            widget.set_highlights(rects, cur)

    # -- cleanup -----------------------------------------------------------
    def closeEvent(self, event) -> None:
        self.close_document()
        self.pool.waitForDone(1200)
        super().closeEvent(event)
