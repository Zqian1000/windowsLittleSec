"""胶囊下方的小提示条：几秒后自己淡出，不接收点击，也不写任何数据。"""
from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QRect, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class Toast(QWidget):
    LIFE_MS = 360
    FADE_MS = 120

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowStaysOnTopHint
                            | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._text = ""
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._begin_fade)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(self.FADE_MS)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.hide)

    def set_text(self, text: str) -> None:
        self._text = text
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setBold(True)
        width = max(76, QFontMetrics(f).horizontalAdvance(text) + 26)
        self.setFixedSize(width, 26)
        self.update()

    def show_at(self, anchor: QRect) -> None:
        self._fade.stop()
        self.setWindowOpacity(1.0)
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = anchor.center().x() - self.width() // 2
        y = anchor.bottom() + 6
        x = max(geo.left() + 4, min(x, geo.right() - self.width() - 4))
        if y + self.height() > geo.bottom():
            y = max(geo.top() + 4, anchor.top() - self.height() - 6)
        self.move(x, y)
        self.show()
        self.raise_()
        self._hide_timer.start(self.LIFE_MS)

    def _begin_fade(self) -> None:
        if not self.isVisible():
            return
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.start()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.setPen(QPen(QColor(255, 196, 110, 90), 1))
        p.setBrush(QColor(28, 28, 30, 238))
        p.drawRoundedRect(r.adjusted(0, 0, -1, -1), 11, 11)
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffd08a"))
        p.drawText(r, Qt.AlignCenter, self._text)
        p.end()
