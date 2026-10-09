"""
Signature capture and placement.

  * :class:`SignaturePad`      draw a signature with the mouse or a pen tablet
  * :class:`SignatureDialog`   draw / type / upload, then save it for reuse
  * :class:`SignaturePlacer`   drag and resize the signature onto a page
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QFontDatabase, QImage,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSizePolicy, QSlider,
                               QTabWidget, QVBoxLayout, QWidget)

from app import db, paths
from app import branding
from app.core import utils
from app.core.pdfbase import open_pdf, pymupdf

def _accent_rgba(alpha: int) -> QColor:
    """The brand accent at a given alpha — keeps selection tints on-brand."""
    c = QColor(branding.BRAND_ACCENT)
    c.setAlpha(alpha)
    return c


SIGNATURE_FONTS = ["Segoe Script", "Brush Script MT", "Lucida Handwriting",
                   "Gabriola", "Ink Free", "Georgia", "Palatino Linotype"]


# ---------------------------------------------------------------------------
# drawing pad
# ---------------------------------------------------------------------------

class SignaturePad(QFrame):
    """Free-hand drawing surface with pressure-free smoothing."""

    changed = Signal()

    def __init__(self, parent=None, width: int = 620, height: int = 200):
        super().__init__(parent)
        self.setObjectName("CardFlat")
        self.setFixedSize(width, height)
        self.setCursor(QCursor(Qt.CrossCursor))
        self._strokes: list[list[QPointF]] = []
        self._current: list[QPointF] = []
        self._pen_width = 2.8
        self._color = QColor("#12305C")

    def set_pen_width(self, value: float) -> None:
        self._pen_width = max(0.6, float(value))
        self.update()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def clear(self) -> None:
        self._strokes.clear()
        self._current.clear()
        self.update()
        self.changed.emit()

    def undo_stroke(self) -> None:
        if self._strokes:
            self._strokes.pop()
            self.update()
            self.changed.emit()

    @property
    def is_empty(self) -> bool:
        return not self._strokes and not self._current

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._current = [event.position()]
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton and self._current:
            self._current.append(event.position())
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._current:
            if len(self._current) > 1:
                self._strokes.append(self._current)
            self._current = []
            self.update()
            self.changed.emit()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#FFFFFF"))

        # baseline guide
        p.setPen(QPen(QColor("#D8DEE9"), 1, Qt.DashLine))
        y = int(self.height() * 0.74)
        p.drawLine(24, y, self.width() - 24, y)
        p.setPen(QColor("#B4BECC"))
        p.drawText(QRect(24, y + 4, 200, 18), Qt.AlignLeft, "Sign above the line")

        pen = QPen(self._color, self._pen_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        for stroke in self._strokes + ([self._current] if self._current else []):
            if len(stroke) < 2:
                continue
            path = QPainterPath(stroke[0])
            for i in range(1, len(stroke) - 1):
                mid = QPointF((stroke[i].x() + stroke[i + 1].x()) / 2,
                              (stroke[i].y() + stroke[i + 1].y()) / 2)
                path.quadTo(stroke[i], mid)
            path.lineTo(stroke[-1])
            p.drawPath(path)
        p.end()

    def to_image(self, trim: bool = True, scale: int = 3) -> QImage | None:
        """Render at higher resolution with a transparent background."""
        if self.is_empty:
            return None
        img = QImage(self.width() * scale, self.height() * scale,
                     QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.scale(scale, scale)
        p.setPen(QPen(self._color, self._pen_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for stroke in self._strokes:
            if len(stroke) < 2:
                continue
            path = QPainterPath(stroke[0])
            for i in range(1, len(stroke) - 1):
                mid = QPointF((stroke[i].x() + stroke[i + 1].x()) / 2,
                              (stroke[i].y() + stroke[i + 1].y()) / 2)
                path.quadTo(stroke[i], mid)
            path.lineTo(stroke[-1])
            p.drawPath(path)
        p.end()
        return _trim(img) if trim else img


def _trim(img: QImage, padding: int = 8) -> QImage:
    """Crop transparent borders so the signature scales predictably."""
    if img.isNull():
        return img
    x0, y0 = img.width(), img.height()
    x1 = y1 = 0
    step = max(1, img.width() // 900)
    for y in range(0, img.height(), step):
        for x in range(0, img.width(), step):
            if (img.pixelColor(x, y).alpha()) > 8:
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)
    if x1 <= x0 or y1 <= y0:
        return img
    rect = QRect(max(0, x0 - padding), max(0, y0 - padding),
                 min(img.width(), x1 - x0 + 2 * padding),
                 min(img.height(), y1 - y0 + 2 * padding))
    return img.copy(rect)


def render_typed_signature(text: str, font_family: str, color: str = "#12305C",
                           point_size: int = 64) -> QImage | None:
    """Render typed text as a signature image."""
    text = (text or "").strip()
    if not text:
        return None
    font = QFont(font_family, point_size)
    font.setStyleStrategy(QFont.PreferAntialias)
    probe = QImage(10, 10, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(probe)
    p.setFont(font)
    metrics = p.fontMetrics()
    w = metrics.horizontalAdvance(text) + 60
    h = metrics.height() + 40
    p.end()

    img = QImage(max(60, w), max(40, h), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.setFont(font)
    p.setPen(QColor(color))
    p.drawText(img.rect(), Qt.AlignCenter, text)
    p.end()
    return _trim(img)


def available_signature_fonts() -> list[str]:
    families = set(QFontDatabase.families())
    found = [f for f in SIGNATURE_FONTS if f in families]
    return found or sorted(families)[:12]


# ---------------------------------------------------------------------------
# capture dialog
# ---------------------------------------------------------------------------

class SignatureDialog(QDialog):
    """Draw, type or upload a signature and optionally save it for reuse."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create signature")
        self.setMinimumWidth(690)
        self.result_path: Path | None = None
        self.result_kind: str = "drawn"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)

        # --- draw ---------------------------------------------------------
        draw_tab = QWidget()
        dl = QVBoxLayout(draw_tab)
        dl.setSpacing(10)
        self.pad = SignaturePad()
        dl.addWidget(self.pad, 0, Qt.AlignCenter)

        row = QHBoxLayout()
        row.addWidget(QLabel("Pen thickness"))
        self.width_slider = QSlider(Qt.Horizontal)
        self.width_slider.setRange(10, 90)
        self.width_slider.setValue(28)
        self.width_slider.setFixedWidth(150)
        self.width_slider.valueChanged.connect(lambda v: self.pad.set_pen_width(v / 10))
        row.addWidget(self.width_slider)
        row.addStretch(1)
        undo = QPushButton("Undo stroke")
        undo.setObjectName("Ghost")
        undo.clicked.connect(self.pad.undo_stroke)
        clear = QPushButton("Clear")
        clear.setObjectName("Ghost")
        clear.clicked.connect(self.pad.clear)
        row.addWidget(undo)
        row.addWidget(clear)
        dl.addLayout(row)
        self.tabs.addTab(draw_tab, "Draw")

        # --- type ---------------------------------------------------------
        type_tab = QWidget()
        tl = QVBoxLayout(type_tab)
        tl.setSpacing(10)
        self.type_edit = QLineEdit()
        self.type_edit.setPlaceholderText("Type your name")
        self.type_edit.textChanged.connect(self._update_typed_preview)
        tl.addWidget(self.type_edit)

        frow = QHBoxLayout()
        frow.addWidget(QLabel("Style"))
        self.font_combo = QComboBox()
        self.font_combo.addItems(available_signature_fonts())
        self.font_combo.currentTextChanged.connect(self._update_typed_preview)
        frow.addWidget(self.font_combo, 1)
        tl.addLayout(frow)

        self.typed_preview = QLabel("Preview appears here")
        self.typed_preview.setObjectName("CardFlat")
        self.typed_preview.setAlignment(Qt.AlignCenter)
        self.typed_preview.setMinimumHeight(150)
        self.typed_preview.setStyleSheet("background:#FFFFFF;border-radius:10px;")
        tl.addWidget(self.typed_preview)
        self.tabs.addTab(type_tab, "Type")

        # --- upload -------------------------------------------------------
        up_tab = QWidget()
        ul = QVBoxLayout(up_tab)
        ul.setSpacing(10)
        pick = QPushButton("Choose an image file…")
        pick.setObjectName("Primary")
        pick.clicked.connect(self._pick_image)
        ul.addWidget(pick, 0, Qt.AlignLeft)
        hint = QLabel("A PNG with a transparent background gives the cleanest result. "
                      "JPG, BMP, TIFF and WEBP also work.")
        hint.setObjectName("Subtle")
        hint.setWordWrap(True)
        ul.addWidget(hint)
        self.upload_preview = QLabel("No image selected")
        self.upload_preview.setObjectName("CardFlat")
        self.upload_preview.setAlignment(Qt.AlignCenter)
        self.upload_preview.setMinimumHeight(180)
        self.upload_preview.setStyleSheet("background:#FFFFFF;border-radius:10px;")
        ul.addWidget(self.upload_preview)
        self.tabs.addTab(up_tab, "Upload")

        # --- save ---------------------------------------------------------
        save_row = QHBoxLayout()
        save_row.addWidget(QLabel("Save as"))
        self.name_edit = QLineEdit(f"Signature {datetime.now():%d %b %H:%M}")
        save_row.addWidget(self.name_edit, 1)
        lay.addLayout(save_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setObjectName("Primary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self._uploaded: Path | None = None

    def _update_typed_preview(self) -> None:
        img = render_typed_signature(self.type_edit.text(),
                                     self.font_combo.currentText())
        if img is None:
            self.typed_preview.setText("Preview appears here")
            self.typed_preview.setPixmap(QPixmap())
            return
        pm = QPixmap.fromImage(img).scaled(520, 130, Qt.KeepAspectRatio,
                                           Qt.SmoothTransformation)
        self.typed_preview.setPixmap(pm)

    def _pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a signature image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)")
        if not path:
            return
        self._uploaded = Path(path)
        pm = QPixmap(path)
        if pm.isNull():
            QMessageBox.warning(self, "Unsupported image", "That file could not be read.")
            self._uploaded = None
            return
        self.upload_preview.setPixmap(pm.scaled(520, 160, Qt.KeepAspectRatio,
                                                Qt.SmoothTransformation))

    def _accept(self) -> None:
        idx = self.tabs.currentIndex()
        image: QImage | None = None
        kind = "drawn"

        if idx == 0:
            image = self.pad.to_image()
            kind = "drawn"
            if image is None:
                QMessageBox.information(self, "Nothing drawn",
                                        "Draw your signature before saving.")
                return
        elif idx == 1:
            image = render_typed_signature(self.type_edit.text(),
                                           self.font_combo.currentText())
            kind = "typed"
            if image is None:
                QMessageBox.information(self, "Nothing typed",
                                        "Type your name before saving.")
                return
        else:
            if not self._uploaded:
                QMessageBox.information(self, "No image",
                                        "Choose an image file before saving.")
                return
            image = QImage(str(self._uploaded))
            kind = "image"
            if image.isNull():
                QMessageBox.warning(self, "Unsupported image",
                                    "That file could not be read.")
                return

        name = self.name_edit.text().strip() or f"Signature {datetime.now():%d %b %H:%M}"
        target = utils.unique_path(
            paths.signatures_dir() / f"{utils.sanitize_filename(name)}.png")
        if not image.save(str(target), "PNG"):
            QMessageBox.warning(self, "Could not save",
                                "The signature image could not be written.")
            return

        try:
            db.add_signature(name, kind, target)
        except Exception:
            pass
        self.result_path = target
        self.result_kind = kind
        self.accept()


# ---------------------------------------------------------------------------
# placement canvas
# ---------------------------------------------------------------------------

class SignaturePlacer(QFrame):
    """Page preview onto which the signature box is dragged and resized."""

    placement_changed = Signal(int, tuple)      # page index, rect in PDF points
    page_loaded = Signal(int, int)

    HANDLE = 9

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ViewerCanvas")
        self.setMinimumSize(420, 500)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

        self._doc = None
        self._index = 0
        self._pixmap: QPixmap | None = None
        self._page_rect = QRect()
        self._sig: QPixmap | None = None
        self._sig_path: Path | None = None
        self._box = QRectF(0.12, 0.72, 0.30, 0.09)     # relative
        self._drag: str | None = None
        self._origin = QPoint()
        self._box_origin = QRectF(self._box)

    # -- document ----------------------------------------------------------
    def load(self, path: str | Path, password: str | None = None) -> bool:
        self.close_document()
        try:
            self._doc = open_pdf(path, password)
        except Exception:
            return False
        self._index = 0
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
        self._emit()

    @property
    def page_index(self) -> int:
        return self._index

    @property
    def page_count(self) -> int:
        return self._doc.page_count if self._doc else 0

    def set_signature(self, path: str | Path | None) -> None:
        if path is None:
            self._sig = None
            self._sig_path = None
        else:
            pm = QPixmap(str(path))
            self._sig = None if pm.isNull() else pm
            self._sig_path = Path(path) if self._sig else None
            if self._sig is not None:
                # keep the aspect ratio of the signature image
                ratio = self._sig.height() / max(1, self._sig.width())
                self._box.setHeight(self._box.width() * ratio *
                                    (self._page_aspect() or 0.707))
        self.update()
        self._emit()

    def _page_aspect(self) -> float:
        if self._doc is None:
            return 0.707
        r = self._doc[self._index].rect
        return r.width / max(1.0, r.height)

    def _render(self) -> None:
        if self._doc is None:
            return
        try:
            page = self._doc[self._index]
            r = page.rect
            zoom = min(max(80, self.width() - 40) / max(1.0, r.width),
                       max(80, self.height() - 40) / max(1.0, r.height))
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

    # -- geometry ----------------------------------------------------------
    def rect_points(self) -> tuple[float, float, float, float]:
        if self._doc is None:
            return (0, 0, 0, 0)
        r = self._doc[self._index].rect
        return (self._box.left() * r.width, self._box.top() * r.height,
                self._box.right() * r.width, self._box.bottom() * r.height)

    def _emit(self) -> None:
        self.placement_changed.emit(self._index, self.rect_points())

    def _screen_box(self) -> QRect:
        pr = self._page_rect
        return QRect(int(pr.left() + self._box.left() * pr.width()),
                     int(pr.top() + self._box.top() * pr.height()),
                     max(10, int(self._box.width() * pr.width())),
                     max(8, int(self._box.height() * pr.height())))

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#DFE4EC"))

        if self._pixmap is None:
            p.setPen(QColor("#8792AB"))
            p.drawText(self.rect(), Qt.AlignCenter, "Open a PDF to place your signature")
            p.end()
            return

        x = (self.width() - self._pixmap.width()) // 2
        y = (self.height() - self._pixmap.height()) // 2
        self._page_rect = QRect(x, y, self._pixmap.width(), self._pixmap.height())
        p.drawPixmap(self._page_rect, self._pixmap)

        box = self._screen_box()
        if self._sig is not None:
            p.drawPixmap(box, self._sig.scaled(box.size(), Qt.IgnoreAspectRatio,
                                               Qt.SmoothTransformation))
        else:
            p.fillRect(box, _accent_rgba(26))
            p.setPen(QColor(branding.BRAND_ACCENT))
            p.drawText(box, Qt.AlignCenter, "Signature")

        p.setPen(QPen(QColor(branding.BRAND_ACCENT), 1.4, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(box)
        p.setPen(QPen(QColor(branding.BRAND_ACCENT), 1))
        p.setBrush(QColor("#FFFFFF"))
        for pt in self._handles(box).values():
            p.drawRect(QRect(pt.x() - self.HANDLE // 2, pt.y() - self.HANDLE // 2,
                             self.HANDLE, self.HANDLE))
        p.end()

    def _handles(self, rect: QRect) -> dict[str, QPoint]:
        return {
            "tl": QPoint(rect.left(), rect.top()),
            "tr": QPoint(rect.right(), rect.top()),
            "bl": QPoint(rect.left(), rect.bottom()),
            "br": QPoint(rect.right(), rect.bottom()),
        }

    # -- interaction -------------------------------------------------------
    def _hit(self, pos: QPoint) -> str | None:
        box = self._screen_box()
        for name, pt in self._handles(box).items():
            if abs(pos.x() - pt.x()) <= self.HANDLE and abs(pos.y() - pt.y()) <= self.HANDLE:
                return name
        return "move" if box.contains(pos) else None

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        self._drag = self._hit(pos)
        if self._drag is None and self._page_rect.contains(pos):
            # click on empty page area re-centres the box there
            pr = self._page_rect
            w, h = self._box.width(), self._box.height()
            self._box = QRectF(
                utils.clamp((pos.x() - pr.left()) / pr.width() - w / 2, 0.0, 1.0 - w),
                utils.clamp((pos.y() - pr.top()) / pr.height() - h / 2, 0.0, 1.0 - h),
                w, h)
            self.update()
            self._emit()
            return
        self._origin = pos
        self._box_origin = QRectF(self._box)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._drag is None:
            hit = self._hit(pos)
            self.setCursor(QCursor(
                Qt.SizeAllCursor if hit == "move" else
                Qt.SizeFDiagCursor if hit in ("tl", "br") else
                Qt.SizeBDiagCursor if hit in ("tr", "bl") else Qt.ArrowCursor))
            return

        pr = self._page_rect
        if pr.width() <= 0:
            return
        dx = (pos.x() - self._origin.x()) / pr.width()
        dy = (pos.y() - self._origin.y()) / pr.height()
        b = QRectF(self._box_origin)

        if self._drag == "move":
            b.moveLeft(utils.clamp(b.left() + dx, 0.0, 1.0 - b.width()))
            b.moveTop(utils.clamp(b.top() + dy, 0.0, 1.0 - b.height()))
        else:
            left, top = b.left(), b.top()
            right, bottom = b.right(), b.bottom()
            if "l" in self._drag:
                left = utils.clamp(left + dx, 0.0, right - 0.04)
            if "r" in self._drag:
                right = utils.clamp(right + dx, left + 0.04, 1.0)
            if "t" in self._drag:
                top = utils.clamp(top + dy, 0.0, bottom - 0.02)
            if "b" in self._drag:
                bottom = utils.clamp(bottom + dy, top + 0.02, 1.0)
            b = QRectF(left, top, right - left, bottom - top)

        self._box = b
        self.update()
        self._emit()

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        self.setCursor(QCursor(Qt.ArrowCursor))
