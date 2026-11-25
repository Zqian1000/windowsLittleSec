"""播放中的小歌词条：贴在岛下方的一条细长胶囊，只显示当前这一句。

不接收鼠标（WindowTransparentForInput），不会挡住下面的窗口；
没有歌词或不在播放时自动隐藏。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

BG = "#141416"
TEXT = "#f5f5f7"


class LyricBar(QWidget):
    HEIGHT = 30

    def __init__(self, parent=None) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle("小二歌词")
        self._text = ""
        self.setFixedHeight(self.HEIGHT)
        self.resize(460, self.HEIGHT)
        self.hide()

    def set_text(self, text: str) -> None:
        text = (text or "").strip()
        if text == self._text:
            return
        self._text = text
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setBrush(QBrush(QColor(BG)))
        p.setPen(Qt.NoPen)
        p.setOpacity(0.92)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0, 0, 0, 0), 15, 15)
        p.setOpacity(1.0)
        font = QFont()
        font.setPointSize(9)
        p.setFont(font)
        p.setPen(QColor(TEXT))
        # 文本超长时居省略，宽度始终跟岛对齐
        avail = max(40, self.width() - 32)
        shown = self.fontMetrics().elidedText(self._text, Qt.ElideRight, avail)
        p.drawText(self.rect(), Qt.AlignCenter, shown)
        p.end()

    def follow(self, island_geo) -> None:
        """贴在岛正下方，宽度与岛一致、左边缘对齐。"""
        width = max(120, island_geo.width())
        if self.width() != width:
            self.setFixedWidth(width)
        self.move(int(island_geo.left()), int(island_geo.bottom() + 6))

    def stick_top(self, x: int, width: int, top: int = 0) -> None:
        """岛收上去躲起来时，歌词自己贴住屏幕最上沿，别悬在半空。"""
        width = max(120, width)
        if self.width() != width:
            self.setFixedWidth(width)
        self.move(int(x), int(top))
