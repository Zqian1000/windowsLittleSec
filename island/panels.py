"""贴在胶囊下方弹出的卡片基类。

几个工作面板共用同一套无边框、置顶、贴边回收的定位逻辑；背景统一画成
16px 圆角深色卡片。子类只管往里放控件或自绘内容。
"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QWidget

BG = "#1c1c1e"
TEXT = "#f2f2f7"
SUBTEXT = "#98989f"
ACCENT = "#0a84ff"


class PopupCard(QWidget):
    closed = Signal()
    WIDTH = 440

    def __init__(self, height: int = 320, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedWidth(self.WIDTH)
        self.setFixedHeight(height)
        self.auto_dismiss = True
        self.pinned = False
        self.setMouseTracking(True)
        esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc.setContext(Qt.WidgetWithChildrenShortcut)
        esc.activated.connect(self.close_panel)

    def popup_near(self, anchor: QRect) -> None:
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = anchor.center().x() - self.width() // 2
        y = anchor.bottom() + 8
        x = max(geo.left() + 6, min(x, geo.right() - self.width() - 6))
        if y + self.height() > geo.bottom():
            y = max(geo.top() + 6, anchor.top() - self.height() - 8)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def close_panel(self) -> None:
        if not self.isVisible():
            return
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close_panel()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(BG))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 16, 16)
        p.end()
