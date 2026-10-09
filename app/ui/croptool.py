"""
Interactive crop canvas.

Shows a rendered page with a draggable crop rectangle and eight resize handles.
The rectangle is stored as *relative* fractions of the page (0..1) rather than
points, so the same crop applies correctly to pages of differing sizes.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QSizePolicy, QWidget

from app import branding
from app.core import utils
from app.core.pdfbase import open_pdf, pymupdf

HANDLE = 9
MIN_FRACTION = 0.03


class CropCanvas(QFrame):
    """Page preview with a live crop rectangle."""

    crop_changed = Signal(tuple)          # (l, t, r, b) as 0..1 fractions
    page_loaded = Signal(int, int)        # page index, page count

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ViewerCanvas")
        self.setMinimumSize(420, 520)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

        self._doc = None
        self._path: Path | None = None
        self._index = 0
        self._pixmap: QPixmap | None = None
        self._page_rect = QRect()             # where the page is drawn, in widget coords
        self._crop = (0.0, 0.0, 1.0, 1.0)
        self._drag: str | None = None
        self._drag_origin = QPoint()
        self._crop_origin = self._crop

    # -- document ----------------------------------------------------------
    def load(self, path: str | Path, password: str | None = None, index: int = 0) -> bool:
        self.close_document()
        try:
            self._doc = open_pdf(path, password)
        except Exception:
            return False
        self._path = Path(path)
        self._index = int(utils.clamp(index, 0, self._doc.page_count - 1))
        self._crop = (0.0, 0.0, 1.0, 1.0)
        self._render()
        self.page_loaded.emit(self._index, self._doc.page_count)
        return True

    def close_document(self) -> None:
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass
        self._doc = None
        self._pixmap = None
        self.update()

    def set_page(self, index: int) -> None:
        if self._doc is None:
            return
        self._index = int(utils.clamp(index, 0, self._doc.page_count - 1))
        self._render()
        self.page_loaded.emit(self._index, self._doc.page_count)

    @property
    def page_index(self) -> int:
        return self._index

    @property
    def page_count(self) -> int:
        return self._doc.page_count if self._doc else 0

    def page_size_points(self) -> tuple[float, float]:
        if self._doc is None:
            return (595.0, 842.0)
        r = self._doc[self._index].rect
        return (r.width, r.height)

    def _render(self) -> None:
        if self._doc is None:
            return
        try:
            page = self._doc[self._index]
            r = page.rect
            avail_w = max(80, self.width() - 48)
            avail_h = max(80, self.height() - 48)
            zoom = min(avail_w / max(1.0, r.width), avail_h / max(1.0, r.height))
            zoom = max(0.08, min(zoom, 4.0))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                         QImage.Format_RGB888).copy()
            self._pixmap = QPixmap.fromImage(img)
        except Exception:
            self._pixmap = None
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render()

    # -- crop --------------------------------------------------------------
    def crop(self) -> tuple[float, float, float, float]:
        return self._crop

    def set_crop(self, value: tuple[float, float, float, float]) -> None:
        l, t, r, b = value
        l, r = min(l, r), max(l, r)
        t, b = min(t, b), max(t, b)
        self._crop = (
            float(utils.clamp(l, 0.0, 1.0 - MIN_FRACTION)),
            float(utils.clamp(t, 0.0, 1.0 - MIN_FRACTION)),
            float(utils.clamp(r, MIN_FRACTION, 1.0)),
            float(utils.clamp(b, MIN_FRACTION, 1.0)),
        )
        self.update()
        self.crop_changed.emit(self._crop)

    def reset_crop(self) -> None:
        self.set_crop((0.0, 0.0, 1.0, 1.0))

    def set_margins_points(self, left: float, top: float, right: float, bottom: float) -> None:
        w, h = self.page_size_points()
        self.set_crop((left / w, top / h, 1 - right / w, 1 - bottom / h))

    def margins_points(self) -> tuple[float, float, float, float]:
        w, h = self.page_size_points()
        l, t, r, b = self._crop
        return (l * w, t * h, (1 - r) * w, (1 - b) * h)

    def auto_detect(self, padding: float = 6.0) -> bool:
        """Set the crop to the page's ink bounds."""
        if self._doc is None:
            return False
        from app.core.pdf_ops import _content_bbox

        page = self._doc[self._index]
        bbox = _content_bbox(page)
        if bbox is None:
            return False
        r = page.rect
        self.set_crop((
            max(0.0, (bbox.x0 - padding - r.x0) / r.width),
            max(0.0, (bbox.y0 - padding - r.y0) / r.height),
            min(1.0, (bbox.x1 + padding - r.x0) / r.width),
            min(1.0, (bbox.y1 + padding - r.y0) / r.height),
        ))
        return True

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#20252E") if self._dark() else QColor("#DFE4EC"))

        if self._pixmap is None:
            p.setPen(QColor("#8792AB"))
            p.drawText(self.rect(), Qt.AlignCenter, "Open a PDF to start cropping")
            p.end()
            return

        x = (self.width() - self._pixmap.width()) // 2
        y = (self.height() - self._pixmap.height()) // 2
        self._page_rect = QRect(x, y, self._pixmap.width(), self._pixmap.height())
        p.drawPixmap(self._page_rect, self._pixmap)

        crop_rect = self._crop_rect()
        # dim everything outside the crop
        overlay = QColor(10, 14, 22, 132)
        pr = self._page_rect
        p.fillRect(QRect(pr.left(), pr.top(), pr.width(), crop_rect.top() - pr.top()), overlay)
        p.fillRect(QRect(pr.left(), crop_rect.bottom(), pr.width(),
                         pr.bottom() - crop_rect.bottom()), overlay)
        p.fillRect(QRect(pr.left(), crop_rect.top(), crop_rect.left() - pr.left(),
                         crop_rect.height()), overlay)
        p.fillRect(QRect(crop_rect.right(), crop_rect.top(),
                         pr.right() - crop_rect.right(), crop_rect.height()), overlay)

        p.setPen(QPen(QColor(branding.BRAND_ACCENT), 1.6))
        p.drawRect(crop_rect)

        # rule-of-thirds guides
        p.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.DashLine))
        for i in (1, 2):
            gx = crop_rect.left() + crop_rect.width() * i / 3
            gy = crop_rect.top() + crop_rect.height() * i / 3
            p.drawLine(int(gx), crop_rect.top(), int(gx), crop_rect.bottom())
            p.drawLine(crop_rect.left(), int(gy), crop_rect.right(), int(gy))

        p.setPen(QPen(QColor(branding.BRAND_ACCENT), 1))
        p.setBrush(QColor("#FFFFFF"))
        for pt in self._handle_points(crop_rect).values():
            p.drawRect(QRect(pt.x() - HANDLE // 2, pt.y() - HANDLE // 2, HANDLE, HANDLE))

        w_pt, h_pt = self.page_size_points()
        l, t, r, b = self._crop
        text = (f"{(r - l) * w_pt:.0f} × {(b - t) * h_pt:.0f} pt   "
                f"({(r - l) * 100:.0f}% × {(b - t) * 100:.0f}%)")
        p.setPen(QColor("#FFFFFF"))
        badge = QRect(crop_rect.left(), max(2, crop_rect.top() - 24), 210, 20)
        p.fillRect(badge, QColor(20, 27, 45, 200))
        p.drawText(badge.adjusted(8, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
        p.end()

    def _dark(self) -> bool:
        return self.palette().window().color().lightness() < 128

    def _crop_rect(self) -> QRect:
        pr = self._page_rect
        l, t, r, b = self._crop
        return QRect(
            int(pr.left() + l * pr.width()),
            int(pr.top() + t * pr.height()),
            max(4, int((r - l) * pr.width())),
            max(4, int((b - t) * pr.height())),
        )

    def _handle_points(self, rect: QRect) -> dict[str, QPoint]:
        cx = rect.center().x()
        cy = rect.center().y()
        return {
            "tl": QPoint(rect.left(), rect.top()),
            "tr": QPoint(rect.right(), rect.top()),
            "bl": QPoint(rect.left(), rect.bottom()),
            "br": QPoint(rect.right(), rect.bottom()),
            "t": QPoint(cx, rect.top()),
            "b": QPoint(cx, rect.bottom()),
            "l": QPoint(rect.left(), cy),
            "r": QPoint(rect.right(), cy),
        }

    # -- interaction -------------------------------------------------------
    CURSORS = {
        "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
        "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
        "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
        "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
        "move": Qt.SizeAllCursor,
    }

    def _hit(self, pos: QPoint) -> str | None:
        if self._pixmap is None:
            return None
        rect = self._crop_rect()
        for name, pt in self._handle_points(rect).items():
            if abs(pos.x() - pt.x()) <= HANDLE and abs(pos.y() - pt.y()) <= HANDLE:
                return name
        if rect.contains(pos):
            return "move"
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        self._drag = self._hit(pos)
        self._drag_origin = pos
        self._crop_origin = self._crop

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._drag is None:
            hit = self._hit(pos)
            self.setCursor(QCursor(self.CURSORS.get(hit, Qt.ArrowCursor)))
            return

        pr = self._page_rect
        if pr.width() <= 0 or pr.height() <= 0:
            return
        dx = (pos.x() - self._drag_origin.x()) / pr.width()
        dy = (pos.y() - self._drag_origin.y()) / pr.height()
        l, t, r, b = self._crop_origin

        if self._drag == "move":
            w, h = r - l, b - t
            l = utils.clamp(l + dx, 0.0, 1.0 - w)
            t = utils.clamp(t + dy, 0.0, 1.0 - h)
            r, b = l + w, t + h
        else:
            if "l" in self._drag:
                l = utils.clamp(l + dx, 0.0, r - MIN_FRACTION)
            if "r" in self._drag:
                r = utils.clamp(r + dx, l + MIN_FRACTION, 1.0)
            if "t" in self._drag:
                t = utils.clamp(t + dy, 0.0, b - MIN_FRACTION)
            if "b" in self._drag:
                b = utils.clamp(b + dy, t + MIN_FRACTION, 1.0)

        self._crop = (l, t, r, b)
        self.update()
        self.crop_changed.emit(self._crop)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        self.setCursor(QCursor(Qt.ArrowCursor))
