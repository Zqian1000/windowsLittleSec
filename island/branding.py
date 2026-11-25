"""小二品牌图标：深色圆盘 + 蓝色渐变环 + 机器人头像。

运行时用 QPainter 画，任意尺寸都清晰，不依赖外部图片文件；
打包 exe 的 .ico 由 tools/make_icon.py 调同一份绘制逻辑生成。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)

DARK = QColor("#151519")
RING_TOP = QColor("#34a0ff")
RING_MID = QColor("#2f6bff")
RING_BOT = QColor("#7a4dff")
ANTENNA = QColor("#6fd0ff")
WHITE = QColor("#ffffff")

SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def render_icon(size: int) -> QImage:
    """画一张 size×size、透明底的图标。"""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(size)

    body = QRectF(s * 0.05, s * 0.05, s * 0.90, s * 0.90)
    p.setBrush(QBrush(DARK))
    p.setPen(Qt.NoPen)
    p.drawEllipse(body)

    # 蓝色渐变圆环
    outer = QPainterPath()
    outer.addEllipse(body)
    inner = QPainterPath()
    inner.addEllipse(body.adjusted(s * 0.055, s * 0.055, -s * 0.055, -s * 0.055))
    grad = QLinearGradient(0, 0, s, s)
    grad.setColorAt(0.0, RING_TOP)
    grad.setColorAt(0.55, RING_MID)
    grad.setColorAt(1.0, RING_BOT)
    p.fillPath(outer.subtracted(inner), QBrush(grad))

    # 头
    hw, hh = s * 0.46, s * 0.34
    head = QRectF(s / 2 - hw / 2, s * 0.36, hw, hh)
    p.setBrush(QBrush(WHITE))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(head, s * 0.10, s * 0.10)

    # 天线
    p.setPen(QPen(WHITE, max(1.0, s * 0.035)))
    p.drawLine(QPointF(s / 2, s * 0.24), QPointF(s / 2, s * 0.34))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(ANTENNA))
    dia = s * 0.09
    p.drawEllipse(QRectF(s / 2 - dia / 2, s * 0.17, dia, dia))

    # 眼睛：小尺寸略放大，避免糊成一团
    eye = max(1.4, s * (0.075 if size >= 32 else 0.09))
    ey = head.center().y() - eye / 2
    p.setBrush(QBrush(DARK))
    p.drawRoundedRect(QRectF(s / 2 - s * 0.10 - eye / 2, ey, eye, eye), eye * 0.3, eye * 0.3)
    p.drawRoundedRect(QRectF(s / 2 + s * 0.10 - eye / 2, ey, eye, eye), eye * 0.3, eye * 0.3)
    p.end()
    return img


def app_icon() -> QIcon:
    """多尺寸 QIcon：托盘、任务栏、Alt-Tab 都会挑最合适的一档。"""
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(QPixmap.fromImage(render_icon(size)))
    return icon
