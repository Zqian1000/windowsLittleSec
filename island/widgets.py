"""岛上面的基础自绘控件：电量环、带红点的联系人图标。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPoint, QPointF, Signal
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetrics, QGuiApplication,
    QPolygonF, QPainterPath,
)
from PySide6.QtWidgets import QPushButton, QWidget

# 客户端配色
CLIENT_COLORS = {
    "wechat": "#22c55e",
    "qq": "#12b7f5",
    "wecom": "#1d6fe0",
    "feishu": "#00c1d4",
    "dingtalk": "#ff8a3d",
}
CLIENT_GLYPH = {
    "wechat": "微",
    "qq": "Q",
    "wecom": "企",
    "feishu": "飞",
    "dingtalk": "钉",
}


def draw_glyph(p: QPainter, kind: str, rect: QRectF, color: QColor) -> None:
    """在任意矩形里画 24px 线性图标，工具箱和胶囊共用同一套图形。"""
    p.save()
    p.translate(rect.x(), rect.y())
    p.scale(rect.width() / 24.0, rect.height() / 24.0)
    p.setPen(QPen(color, 1.5))
    p.setBrush(Qt.NoBrush)
    cx, cy = 12.0, 12.0

    if kind == "clip":
        body = QRectF(cx - 6, cy - 7, 12, 14)
        p.drawRoundedRect(body, 2.5, 2.5)
        p.drawRoundedRect(QRectF(cx - 2.5, cy - 9, 5, 3.5), 1.5, 1.5)
        for dy in (-2, 1.5, 5):
            p.drawLine(QPointF(cx - 3.2, cy + dy), QPointF(cx + 3.2, cy + dy))
    elif kind == "snip":
        arm = 4.5
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            x = cx + sx * 7
            y = cy + sy * 7
            p.drawLine(QPointF(x, y), QPointF(x - sx * arm, y))
            p.drawLine(QPointF(x, y), QPointF(x, y - sy * arm))
        p.drawEllipse(QPointF(cx, cy), 1.4, 1.4)
    elif kind == "bell":
        path = QPainterPath()
        path.moveTo(cx - 6, cy + 4)
        path.cubicTo(cx - 4.5, cy + 1, cx - 4.5, cy - 6.5, cx, cy - 7)
        path.cubicTo(cx + 4.5, cy - 6.5, cx + 4.5, cy + 1, cx + 6, cy + 4)
        p.drawPath(path)
        p.drawLine(QPointF(cx - 7, cy + 4), QPointF(cx + 7, cy + 4))
        p.drawEllipse(QPointF(cx, cy + 6.2), 1.5, 1.5)
    elif kind == "json":
        def brace(sign: float) -> QPainterPath:
            path = QPainterPath()
            path.moveTo(cx + sign * 1.8, cy - 6.6)
            path.quadTo(cx + sign * 4.8, cy - 6.0, cx + sign * 5.4, cy - 3.4)
            path.lineTo(cx + sign * 5.4, cy - 1.6)
            path.quadTo(cx + sign * 5.4, cy - 0.4, cx + sign * 3.2, cy)
            path.quadTo(cx + sign * 5.4, cy + 0.4, cx + sign * 5.4, cy + 1.6)
            path.lineTo(cx + sign * 5.4, cy + 3.4)
            path.quadTo(cx + sign * 4.8, cy + 6.0, cx + sign * 1.8, cy + 6.6)
            return path
        p.setPen(QPen(color, 1.4))
        p.drawPath(brace(-1))
        p.drawPath(brace(1))
    elif kind == "excel":
        p.drawRoundedRect(QRectF(cx - 7, cy - 6.5, 14, 13), 2, 2)
        p.drawLine(QPointF(cx - 7, cy - 1), QPointF(cx + 7, cy - 1))
        p.drawLine(QPointF(cx - 1.5, cy - 6.5), QPointF(cx - 1.5, cy + 6.5))
    elif kind == "gear":
        import math
        r_in, r_out = 5.2, 7.2
        p.drawEllipse(QPointF(cx, cy), r_in, r_in)
        p.drawEllipse(QPointF(cx, cy), 2.2, 2.2)
        for i in range(8):
            a = math.radians(i * 45)
            p.drawLine(
                QPointF(cx + math.cos(a) * (r_in + 0.4), cy + math.sin(a) * (r_in + 0.4)),
                QPointF(cx + math.cos(a) * r_out, cy + math.sin(a) * r_out))
    elif kind == "apps":
        for dx in (-6.3, 1.3):
            for dy in (-6.3, 1.3):
                p.drawRoundedRect(QRectF(cx + dx, cy + dy, 5, 5), 1.2, 1.2)
    elif kind == "folder":
        p.drawRoundedRect(QRectF(cx - 7, cy - 4, 14, 9), 2, 2)
        p.drawLine(QPointF(cx - 7, cy - 4), QPointF(cx - 5, cy - 7))
        p.drawLine(QPointF(cx - 5, cy - 7), QPointF(cx - 1.5, cy - 7))
        p.drawLine(QPointF(cx - 1.5, cy - 7), QPointF(cx - 0.5, cy - 4))
        p.drawLine(QPointF(cx - 6.5, cy - 1.5), QPointF(cx + 6.5, cy - 1.5))
    elif kind == "globe":
        p.drawEllipse(QPointF(cx, cy), 7, 7)
        p.drawEllipse(QPointF(cx, cy), 3.2, 7)
        p.drawLine(QPointF(cx - 7, cy), QPointF(cx + 7, cy))
        p.drawLine(QPointF(cx - 5.8, cy - 3), QPointF(cx + 5.8, cy - 3))
        p.drawLine(QPointF(cx - 5.8, cy + 3), QPointF(cx + 5.8, cy + 3))
    elif kind == "alert":
        tri = QPainterPath()
        tri.moveTo(cx, cy - 7.5)
        tri.lineTo(cx + 7, cy + 6)
        tri.lineTo(cx - 7, cy + 6)
        tri.closeSubpath()
        p.drawPath(tri)
        p.drawLine(QPointF(cx, cy - 2), QPointF(cx, cy + 1.5))
        p.drawEllipse(QPointF(cx, cy + 3.8), 1.0, 1.0)
    elif kind == "doc":
        path = QPainterPath()
        path.moveTo(cx - 5.5, cy - 7)
        path.lineTo(cx + 2.5, cy - 7)
        path.lineTo(cx + 5.5, cy - 4)
        path.lineTo(cx + 5.5, cy + 7)
        path.lineTo(cx - 5.5, cy + 7)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(cx + 2.5, cy - 7), QPointF(cx + 2.5, cy - 4))
        p.drawLine(QPointF(cx + 2.5, cy - 4), QPointF(cx + 5.5, cy - 4))
        for dy in (-0.5, 2.5, 5.5):
            p.drawLine(QPointF(cx - 3, cy + dy), QPointF(cx + 3, cy + dy))
    else:  # list
        for dy in (-4.5, 0, 4.5):
            p.drawEllipse(QPointF(cx - 4.5, cy + dy), 1.2, 1.2)
            p.drawLine(QPointF(cx - 1, cy + dy), QPointF(cx + 6, cy + dy))
    p.restore()


class BatteryRing(QWidget):
    """电量圆环：圆弧进度 + 中间百分比。无电池时显示 "--"。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._percent: float | None = None
        self._plugged = False
        self.setFixedSize(38, 38)

    def set_value(self, percent: float | None, plugged: bool) -> None:
        self._percent = percent
        self._plugged = plugged
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(3, 3, self.width() - 6, self.height() - 6)
        # 底环
        p.setPen(QPen(QColor("#3a3a3c"), 3))
        p.drawArc(rect, 0, 360 * 16)

        if self._percent is None:
            p.setPen(QPen(QColor("#aeaeb2"), 1))
            p.drawText(rect, Qt.AlignCenter, "--")
            p.end()
            return

        color = QColor("#22c55e") if self._percent > 20 else QColor("#ff453a")
        if self._plugged:
            color = QColor("#30d158")
        p.setPen(QPen(color, 3))
        span = int(360 * 16 * self._percent / 100)
        p.drawArc(rect, 90 * 16, -span)

        p.setPen(QColor("#ffffff"))
        f = QFont()
        f.setPixelSize(11)
        p.setFont(f)
        p.drawText(rect, Qt.AlignCenter, f"{int(self._percent)}")
        p.end()


class ContactButton(QPushButton):
    """圆形客户端图标，右上角可挂未读红点。"""

    def __init__(self, client_key: str, name: str, parent=None) -> None:
        super().__init__(parent)
        self.client_key = client_key
        self._name = name
        self._unread = False
        self._attention = False
        self.setFixedSize(30, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(name)

    def set_unread(self, unread: bool) -> None:
        self._unread = unread
        self.update()

    def set_attention(self, on: bool) -> None:
        """有新消息时闪这一下：外圈描白边，配合定时器开关就是"闪"。"""
        if on == self._attention:
            return
        self._attention = on
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(2, 2, self.width() - 4, self.height() - 4)
        bg = QColor(CLIENT_COLORS.get(self.client_key, "#8e8e93"))
        if self._attention:
            bg = bg.lighter(175)          # 闪的时候底色提亮，余光扫到就能注意
        p.setBrush(QBrush(bg))
        p.setPen(Qt.NoPen)
        p.drawEllipse(rect)

        if self._attention:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 235), 2.4))
            p.drawEllipse(rect.adjusted(0.6, 0.6, -0.6, -0.6))

        p.setPen(QColor("#ffffff"))
        f = QFont()
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        glyph = CLIENT_GLYPH.get(self.client_key, self._name[:1])
        p.drawText(rect, Qt.AlignCenter, glyph)

        if self._unread:
            p.setBrush(QBrush(QColor("#ff3b30")))
            p.setPen(QPen(QColor("#141416"), 1))
            p.drawEllipse(QPointF(self.width() - 6.0, 6.0), 4.6, 4.6)
        p.end()


class HoverTip(QWidget):
    """胶囊图标的悬停名称：无边框小窗，鼠标穿透，不抢焦点。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._text = ""
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(26)

    def show_text(self, text: str, anchor: QPoint) -> None:
        self._text = text
        f = QFont(self.font())
        f.setPixelSize(11)
        fm = QFontMetrics(f)
        w = max(46, fm.horizontalAdvance(text) + 20)
        self.resize(w, 26)
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = anchor.x() - w // 2
        y = anchor.y()
        x = max(geo.left() + 4, min(x, geo.right() - w - 4))
        if y + self.height() > geo.bottom():
            y = max(geo.top() + 4, anchor.y() - 32)
        self.move(x, y)
        self.show()
        self.raise_()
        self.update()

    def hide_tip(self) -> None:
        if self.isVisible():
            self.hide()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2c2c2e"))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
        p.setPen(QColor("#f2f2f7"))
        p.setFont(self._font(11))
        p.drawText(self.rect(), Qt.AlignCenter, self._text)
        p.end()

    def _font(self, px: int) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(px)
        return f


class GlyphButton(QPushButton):
    """胶囊上的小工具图标（剪贴板 / 截图 / 提醒 / 列表），线性自绘。

    统一 24px，悬停给一层浅色底；提醒到点时铃铛换成琥珀色并挂一个红点，
    这样不用展开也能看出来"有东西要处理"。
    """

    KINDS = ("clip", "snip", "bell", "list", "json", "excel", "gear",
             "folder", "alert", "doc", "apps", "globe")
    hovered = Signal(str, QPoint)

    def __init__(self, kind: str, tip: str = "", name: str = "", parent=None) -> None:
        super().__init__(parent)
        self._kind = kind if kind in self.KINDS else "clip"
        self._name = name or tip.split("：")[0].split("（")[0]
        self._alert = False
        self._hover = False
        self.setFixedSize(24, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("")

    def set_alert(self, on: bool) -> None:
        if on == self._alert:
            return
        self._alert = bool(on)
        self.update()

    def enterEvent(self, _e) -> None:
        self._hover = True
        self.update()
        if self._name and self.isEnabled():
            self.hovered.emit(
                self._name, self.mapToGlobal(QPoint(self.width() // 2, self.height() + 2)))

    def leaveEvent(self, _e) -> None:
        self._hover = False
        self.update()
        self.hovered.emit("", QPoint())

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._hover:
            p.setBrush(QBrush(QColor(255, 255, 255, 26)))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 7, 7)

        if not self.isEnabled():
            color = QColor("#58585c")
        else:
            color = QColor("#ffd60a") if (self._alert and self._kind == "bell") else (
                QColor("#ffffff") if self._hover else QColor("#c7c7cc"))
        draw_glyph(p, self._kind, QRectF(0, 0, self.width(), self.height()), color)

        if self._alert:
            p.setBrush(QBrush(QColor("#ff453a")))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(self.width() - 4.5, 4.5), 3.2, 3.2)
        p.end()


class MediaButton(QPushButton):
    """媒体控制键：自绘三角/竖条，避免字体缺字形渲染成方块。"""

    KINDS = ("prev", "play", "next", "pause")

    def __init__(self, kind: str, parent=None) -> None:
        super().__init__(parent)
        self._kind = kind if kind in self.KINDS else "play"
        self.setFixedSize(30, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip({"prev": "上一首", "play": "播放", "next": "下一首",
                         "pause": "暂停"}.get(self._kind, ""))

    @property
    def kind(self) -> str:
        return self._kind

    def set_kind(self, kind: str) -> None:
        if kind in self.KINDS and kind != self._kind:
            self._kind = kind
            self.setToolTip({"prev": "上一首", "play": "播放", "next": "下一首",
                             "pause": "暂停"}[kind])
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor("#2c2c2e")))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 15, 15)

        p.setBrush(QBrush(QColor("#ffffff")))
        cx, cy = self.width() / 2, self.height() / 2
        size = 5.0
        if self._kind in ("prev", "next"):
            direction = -1 if self._kind == "prev" else 1
            for shift in (-4.0, 4.0):
                base_x = cx + shift
                p.drawPolygon(QPolygonF([
                    QPointF(base_x - direction * size, cy - size),
                    QPointF(base_x - direction * size, cy + size),
                    QPointF(base_x + direction * size, cy),
                ]))
        elif self._kind == "play":
            tri = QPolygonF([QPointF(cx - 4, cy - 6), QPointF(cx - 4, cy + 6),
                             QPointF(cx + 6, cy)])
            p.drawPolygon(tri)
        else:  # pause
            p.drawRoundedRect(QRectF(cx - 5, cy - 6, 3.5, 12), 1, 1)
            p.drawRoundedRect(QRectF(cx + 1.5, cy - 6, 3.5, 12), 1, 1)
        p.end()
