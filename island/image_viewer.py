"""应用内看图窗口：截图提醒的缩略图点开就是这里，不用跳系统看图软件。

滚轮缩放（以鼠标位置为中心）、按住拖动平移、双击在「适应窗口 / 1:1」之间切换。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QGuiApplication, QKeySequence, QPainter, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

BG = "#0e0e11"
BAR = "#1c1c1e"
TEXT = "#f2f2f7"
SUBTEXT = "#98989f"

_CHIP = ("QPushButton{border:none;border-radius:7px;background:#2c2c2e;"
         "color:#d1d1d6;font-size:11px;padding:4px 10px;}"
         "QPushButton:hover{background:#3a3a3c;color:#ffffff;}")


class _Canvas(QWidget):
    """只负责画图和缩放/平移，不掺窗口逻辑。"""

    zoom_changed = Signal(float)

    def __init__(self, pix: QPixmap, parent=None) -> None:
        super().__init__(parent)
        self.pix = pix
        self.fit = 1.0
        self.zoom = 1.0
        self.pan = QPointF(0.0, 0.0)
        self._drag_from: QPoint | None = None
        self._drag_pan = QPointF()
        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)

    # ---- 几何 ----
    def resizeEvent(self, event) -> None:
        self._recompute_fit()
        self.zoom_changed.emit(self.zoom)
        super().resizeEvent(event)

    def _recompute_fit(self) -> None:
        if self.pix.isNull():
            return
        aw = max(1, self.width() - 24)
        ah = max(1, self.height() - 24)
        self.fit = min(aw / self.pix.width(), ah / self.pix.height())

    def _scale(self) -> float:
        return self.fit * self.zoom

    def reset_fit(self) -> None:
        self.zoom = 1.0
        self.pan = QPointF(0.0, 0.0)
        self.setCursor(Qt.OpenHandCursor)
        self.zoom_changed.emit(self.zoom)
        self.update()

    def actual_size(self) -> None:
        self._recompute_fit()
        if self.fit <= 0:
            return
        self.zoom = min(10.0, 1.0 / self.fit)
        self.pan = QPointF(0.0, 0.0)
        self.zoom_changed.emit(self.zoom)
        self.update()

    # ---- 绘制 ----
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(BG))
        if self.pix.isNull():
            p.end()
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        scale = self._scale()
        w, h = self.pix.width() * scale, self.pix.height() * scale
        x = (self.width() - w) / 2 + self.pan.x()
        y = (self.height() - h) / 2 + self.pan.y()
        p.drawPixmap(QRectF(x, y, w, h), self.pix, QRectF(0, 0, self.pix.width(), self.pix.height()))
        p.end()

    # ---- 交互 ----
    def wheelEvent(self, event) -> None:
        if self.pix.isNull():
            return
        steps = event.angleDelta().y() / 120
        if not steps:
            return
        old = self._scale()
        target = max(0.15, min(10.0, self.zoom * (1.2 ** steps)))
        new = self.fit * target
        pos = event.position()
        center = QPointF(self.width() / 2, self.height() / 2)
        # 让鼠标下面那一点在缩放前后不动
        anchor = (pos - center - self.pan) / old
        self.pan = pos - center - anchor * new
        self.zoom = target
        self.setCursor(Qt.OpenHandCursor if self.zoom > 1.02 else Qt.ArrowCursor)
        self.zoom_changed.emit(self.zoom)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_from = event.position().toPoint()
            self._drag_pan = QPointF(self.pan)
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            self.pan = self._drag_pan + (event.position().toPoint() - self._drag_from)
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_from = None
        self.setCursor(Qt.OpenHandCursor if self.zoom > 1.02 else Qt.ArrowCursor)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self._recompute_fit()
        if abs(self.zoom - 1.0) < 0.01:
            self.actual_size()
        else:
            self.reset_fit()


class ImageViewer(QWidget):
    """一个带深色顶栏的看图窗；同一时间只留一个（show_image 负责复用）。"""

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        self.path = path
        self._pix = QPixmap(path)
        self.setWindowTitle("小二看图")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(f"background:{BG};")

        screen = QGuiApplication.screenAt(QGuiApplication.primaryScreen().geometry().center())
        geo = (screen or QGuiApplication.primaryScreen()).availableGeometry()
        w = min(1100, max(520, int(geo.width() * 0.9)))
        h = min(780, max(360, int(geo.height() * 0.86)))
        self.resize(w, h)
        self.move(geo.left() + (geo.width() - w) // 2, geo.top() + (geo.height() - h) // 3)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        bar = QWidget()
        bar.setFixedHeight(40)
        bar.setStyleSheet(f"background:{BAR};border-top-left-radius:12px;border-top-right-radius:12px;")
        top = QHBoxLayout(bar)
        top.setContentsMargins(12, 0, 8, 0)
        top.setSpacing(8)
        name = QLabel(Path(path).name)
        name.setStyleSheet(f"color:{TEXT};font-size:12px;font-weight:600;")
        size = QLabel(f"{self._pix.width()} × {self._pix.height()}" if not self._pix.isNull() else "读不到图片")
        size.setStyleSheet(f"color:{SUBTEXT};font-size:11px;")
        self.zoom_lab = QLabel("100%")
        self.zoom_lab.setStyleSheet(f"color:{SUBTEXT};font-size:11px;")
        top.addWidget(name)
        top.addWidget(size)
        top.addStretch(1)
        top.addWidget(self.zoom_lab)

        self.btn_fit = QPushButton("适应")
        self.btn_actual = QPushButton("1:1")
        for b, tip in ((self.btn_fit, "缩到能整张看见（双击也可）"), (self.btn_actual, "按原始像素显示")):
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet(_CHIP)
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(24, 22)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setStyleSheet(
            "QPushButton{border:none;background:transparent;color:#98989f;font-size:12px;}"
            "QPushButton:hover{color:#ffffff;}")
        top.addWidget(self.btn_fit)
        top.addWidget(self.btn_actual)
        top.addWidget(self.btn_close)
        outer.addWidget(bar)

        self.canvas = _Canvas(self._pix)
        outer.addWidget(self.canvas, 1)

        self.btn_fit.clicked.connect(self.canvas.reset_fit)
        self.btn_actual.clicked.connect(self.canvas.actual_size)
        self.btn_close.clicked.connect(self.close)
        self.canvas.zoom_changed.connect(self._on_zoom)

        esc = QShortcut(QKeySequence("Esc"), self)
        esc.activated.connect(self.close)

    def _on_zoom(self, zoom: float) -> None:
        self.zoom_lab.setText(f"{int(round(self.canvas.fit * zoom * 100))}%")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 每次打开都先按窗口大小完整显示，缩放/拖动不跨次残留
        self.canvas._recompute_fit()
        self.canvas.reset_fit()
        self.canvas.setFocus()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        if event.key() in (Qt.Key_0, Qt.Key_F):
            self.canvas.reset_fit()
            return
        super().keyPressEvent(event)


_current: ImageViewer | None = None


def current_viewer() -> ImageViewer | None:
    """给 PopupGuard 用：返回当前露着的看图窗（没有就 None）。"""
    try:
        if _current is not None and _current.isVisible():
            return _current
    except RuntimeError:
        pass
    return None


def show_image(path: str) -> None:
    """打开（或替换成）当前看图窗口；文件不存在就什么都不做。"""
    global _current
    if not path or not Path(path).exists():
        return
    if _current is not None:
        try:
            _current.close()
        except RuntimeError:
            pass
    _current = ImageViewer(path)
    _current.show()
    _current.raise_()
    _current.activateWindow()
