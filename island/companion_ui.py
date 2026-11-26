"""胶囊头像：木鱼占位 + 点击抖动。

桌面小伙伴的照片生成/跳舞窗口先停用，头像位置改成一只暖棕色木鱼；
点击时整只木鱼抖动，并弹一个很短的「功德 +1」。不接收照片、不生成
小人、不联网。旧的照片处理与 CompanionWindow 保留在这里，后续接入
用户自有模型时可以复用。
"""
from __future__ import annotations

import sys
import math
import time
from pathlib import Path

from PySide6.QtCore import (
    QPoint, QPointF, QRect, QRectF, Qt, QTimer, QPropertyAnimation, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QGuiApplication, QCursor, QImage, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient,
)
from PySide6.QtWidgets import (
    QFileDialog, QMenu, QPushButton, QToolTip, QWidget,
)

from core.companion import CompanionProfile

IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
ACCENT = "#0a84ff"
ACCENT2 = "#5e5ce6"
TEXT = "#f2f2f7"
SUBTEXT = "#98989f"



def _asset_file(name: str) -> Path:
    """源码运行取项目 assets；PyInstaller onefile 取解包目录。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / name


# ---------- 照片 -> 卡通脸 ----------
def _level_table(levels: int) -> list[int]:
    """把 0-255 压到 levels 档，卡通色块的来源。"""
    levels = max(2, min(12, levels))
    step = 255 / (levels - 1)
    return [int(round(round(v / step) * step)) for v in range(256)]


def _tone_table() -> list[int]:
    """轻微提对比 + 提饱和：照片缩到 56px 后不发灰，也不像贴了张证件照。"""
    out = []
    for v in range(256):
        v = (v - 128) * 1.10 + 129      # 对比
        v = (v - 128) * 1.14 + 128      # 每个通道各自拉伸，等于加饱和
        out.append(max(0, min(255, int(round(v)))))
    return out


def _skin_focus(img: QImage) -> QRect | None:
    """在照片里找最大的一块肤色区域，把裁切框收到脸上。

    只取连通块（头发高光、暖色背景这些零散点不算），半身照也能把脸放大。
    """
    small = img.scaled(96, 96, Qt.IgnoreAspectRatio,
                       Qt.SmoothTransformation).convertToFormat(QImage.Format_RGB32)
    buf = small.constBits()
    stride = small.bytesPerLine()
    mask = bytearray(96 * 96)
    for y in range(96):
        base = y * stride
        for x in range(96):
            i = base + x * 4
            b, g, r = buf[i], buf[i + 1], buf[i + 2]
            mx, mn = max(r, g, b), min(r, g, b)
            if (r > 70 and r > g + 8 and g + 12 >= b
                    and mx - mn > 18 and not (r > 250 and g > 245)):
                mask[y * 96 + x] = 1

    # 最大连通块 = 脸/脖子（头发太暗、衣服太白都不会连进来）
    best: list[int] = []
    seen = bytearray(96 * 96)
    for start in range(96 * 96):
        if not mask[start] or seen[start]:
            continue
        stack = [start]
        seen[start] = 1
        comp: list[int] = []
        while stack:
            cur = stack.pop()
            comp.append(cur)
            cy, cx = divmod(cur, 96)
            for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                if 0 <= nx < 96 and 0 <= ny < 96:
                    n = ny * 96 + nx
                    if mask[n] and not seen[n]:
                        seen[n] = 1
                        stack.append(n)
        if len(comp) > len(best):
            best = comp
    if len(best) < 96 * 96 * 0.012:
        return None

    xs = [c % 96 for c in best]
    ys = [c // 96 for c in best]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    if bw < 6 or bh < 6:
        return None

    # 裁切框宽度按"脸宽"给，而不是按连通块高度：脖子/胸口也连在一起，
    # 用高度会把镜头拉太远，脸又变小。
    bw_n = bw / 96
    side_n = min(0.95, max(0.35, bw_n * 1.35))
    cx = (x0 + x1 + 1) / 2 / 96
    # 肤色块顶部大概是额头/眉毛，往上再多留一点头发
    cy = y0 / 96 + bw_n * 0.50 - bw_n * 0.10
    side = side_n * min(img.width(), img.height())
    x = cx * img.width() - side / 2
    y = cy * img.height() - side / 2
    x = max(0, min(x, img.width() - side))
    y = max(0, min(y, img.height() - side))
    return QRect(int(x), int(y), int(side), int(side))


def _cut_plain_bg(img: QImage) -> QImage:
    """证件照/定妆照这种单色背景，从四边泛洪抠掉，小人不带一圈灰盘子。
    背景不干净（四角色差大）就原样返回，宁可留背景也不乱抠。"""
    w, h = img.width(), img.height()
    if w < 8 or h < 8:
        return img
    img = img.convertToFormat(QImage.Format_ARGB32)
    buf = bytearray(img.constBits())
    stride = img.bytesPerLine()

    def px(x, y):
        i = y * stride + x * 4
        return buf[i], buf[i + 1], buf[i + 2]

    corners = [px(0, 0), px(w - 1, 0), px(0, h - 1), px(w - 1, h - 1)]
    top = [px(0, 0), px(w - 1, 0)]
    # 只看上边两角判断背景：人像下方常是白衬衫/白墙，混进来会把衣服也抠掉
    if (abs(top[0][0] - top[1][0]) + abs(top[0][1] - top[1][1])
            + abs(top[0][2] - top[1][2]) > 60):
        return img
    br = (top[0][0] + top[1][0]) / 2
    bg = (top[0][1] + top[1][1]) / 2
    bb = (top[0][2] + top[1][2]) / 2

    tol = 60
    seen = bytearray(w * h)

    def near(x, y):
        r, g, b = px(x, y)
        return abs(r - br) + abs(g - bg) + abs(b - bb) <= tol

    stack = []
    for x in range(w):
        for y in (0, h - 1):
            if not seen[y * w + x] and near(x, y):
                seen[y * w + x] = 1
                stack.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if not seen[y * w + x] and near(x, y):
                seen[y * w + x] = 1
                stack.append((x, y))
    while stack:
        x, y = stack.pop()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and near(nx, ny):
                seen[ny * w + nx] = 1
                stack.append((nx, ny))
    for y in range(h):
        for x in range(w):
            if seen[y * w + x]:
                buf[y * stride + x * 4 + 3] = 0
    # 边缘那一圈半灰半黑的是抠图过渡色，留着像长毛边：贴着透明像素、颜色又近背景的一律抹掉
    edge = []
    for y in range(h):
        for x in range(w):
            if seen[y * w + x]:
                continue
            r, g, b = px(x, y)
            if abs(r - br) + abs(g - bg) + abs(b - bb) > tol * 1.8:
                continue
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h and seen[ny * w + nx]:
                    edge.append((x, y))
                    break
    for x, y in edge:
        buf[y * stride + x * 4 + 3] = 0
    return QImage(bytes(buf), w, h, stride, QImage.Format_ARGB32)


def prepare_face(src: str, dst: Path, levels: int = 0) -> bool:
    """裁正方形、柔化、微调色彩，存成 PNG。levels>=3 时才压色阶（做卡通块面）。"""
    img = QImage(str(src))
    if img.isNull():
        return False
    box = _skin_focus(img)
    if box is not None:
        img = img.copy(box)
    side = min(img.width(), img.height())
    if side <= 0:
        return False
    img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
    # 先缩到 96 再拉回 168：抹平毛孔/噪点，56px 显示时干净又不糊成一团
    img = img.scaled(96, 96, Qt.IgnoreAspectRatio,
                     Qt.SmoothTransformation).scaled(
        168, 168, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    img = _cut_plain_bg(img.convertToFormat(QImage.Format_RGB32))
    if img.format() != QImage.Format_ARGB32:
        img = img.convertToFormat(QImage.Format_ARGB32)

    # 纯 Python 逐像素太慢，直接改 QImage 的字节缓冲；BGRA 每像素 4 字节
    buf = bytearray(img.constBits())
    table = _level_table(levels) if levels >= 3 else _tone_table()
    for i in range(0, len(buf), 4):
        if buf[i + 3] == 0:
            continue        # 抠掉的背景不用调色
        buf[i] = table[buf[i]]
        buf[i + 1] = table[buf[i + 1]]
        buf[i + 2] = table[buf[i + 2]]

    out = QImage(bytes(buf), img.width(), img.height(),
                 img.bytesPerLine(), QImage.Format_ARGB32)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".tmp.png")
        if not out.save(str(tmp), "PNG"):
            return False
        tmp.replace(dst)
    except OSError:
        return False
    return True


def is_image_file(path: str) -> bool:
    if not path:
        return False
    p = Path(path)
    return p.suffix.lower() in IMG_EXTS and p.exists()


def round_face(pix: QPixmap, size: int) -> QPixmap:
    """把照片裁成圆脸并羽化外圈，贴到小人身上时不带一圈硬邦邦的方底色。"""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    # 裁切框留了肩膀和背景，直接铺圆会变成“灰盘子”；放大一点让脸占满圆
    inner = int(size * 1.45)
    scaled = pix.scaled(inner, inner, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    p.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    p.end()

    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    grad = QRadialGradient(size / 2, size / 2, size / 2)
    grad.setColorAt(0.0, QColor(0, 0, 0, 255))
    grad.setColorAt(0.86, QColor(0, 0, 0, 255))
    grad.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawRect(img.rect())      # 铺满整张，圆外 alpha 归零，否则四角还是原图的方块
    p.end()
    return QPixmap.fromImage(img)


# ---------- 胶囊上的智能体头像 ----------
class MeritToast(QWidget):
    """点头像后闪一下的「功德 +1」。

    只负责显示，不写文件、不计数；猛点就重新计时并拉回不透明，
    不会排一长串、也不会越堆越多。
    """

    W = 76
    H = 26
    LIFE_MS = 320
    FADE_MS = 120

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowStaysOnTopHint
                            | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(self.W, self.H)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._begin_fade)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(self.FADE_MS)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.hide)

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
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor(255, 196, 110, 90), 1))
        p.setBrush(QColor(28, 28, 30, 238))
        p.drawRoundedRect(r, 11, 11)
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffd08a"))
        p.drawText(self.rect(), Qt.AlignCenter, "功德 +1")
        p.end()


class AgentAvatarButton(QPushButton):
    """38px 木鱼：没有外圈，按下就左右抖动两下并弹「功德 +1」。

    连点 20 次会发出 ``easter_egg``，由胶囊决定要不要打开隐藏小游戏。
    间隔超过 1.2 秒就算断掉，避免把一天里的零散点击累加进去。
    """

    easter_egg = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._last_tick = time.monotonic()
        self._click_streak = 0
        self._streak_at = 0.0
        self._alert = False
        self._press = 0.0
        self._jiggle_start = 0.0
        self._jiggle_dur = 0.38
        self.setFixedSize(38, 38)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点一下敲木鱼：功德 +1")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(40)
        self._fish = QPixmap(str(_asset_file("wooden_fish.png")))
        self._toast = MeritToast()
        self.clicked.connect(self._on_click)

    def _animate(self) -> None:
        now = time.monotonic()
        dt = min(0.1, now - self._last_tick)
        self._last_tick = now
        if self._press > 0:
            self._press = max(0.0, self._press - dt * 1.8)
        if self._jiggle_start and now - self._jiggle_start >= self._jiggle_dur:
            self._jiggle_start = 0.0
            self._timer.setInterval(40)
        self.update()

    def _on_click(self) -> None:
        now = time.monotonic()
        if now - self._streak_at <= 1.2:
            self._click_streak += 1
        else:
            self._click_streak = 1
        self._streak_at = now
        if self._click_streak >= 20:
            self._click_streak = 0
            self.easter_egg.emit()
            return
        self._start_jiggle()
        self._touch_merit()

    def _start_jiggle(self) -> None:
        self._jiggle_start = time.monotonic()
        self._timer.setInterval(16)       # 抖动期间 60fps，抖完自动降回来
        self.update()

    def _touch_merit(self) -> None:
        anchor = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        self._toast.show_at(anchor)

    def set_alert(self, on: bool) -> None:
        if bool(on) != self._alert:
            self._alert = bool(on)
            self.update()

    def mousePressEvent(self, e) -> None:
        self._press = 0.5
        if e.button() == Qt.LeftButton:
            # 按下的瞬间就抖，不等松开，手感更跟手
            self._start_jiggle()
        self.update()
        super().mousePressEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        now = time.monotonic()

        scale = 1.0 + 0.04 * self._press
        dx = dy = rot = 0.0
        jiggle_t = 0.0
        if self._jiggle_start:
            jiggle_t = min(1.0, (now - self._jiggle_start) / self._jiggle_dur)
            damp = (1.0 - jiggle_t) ** 1.25
            # 左右各摆一次，带一点倾斜，像被木槌敲得"duang duang"两下
            dx = math.sin(jiggle_t * math.tau * 2.0) * 2.2 * damp
            dy = -abs(math.sin(jiggle_t * math.tau * 2.0)) * 0.7 * damp
            rot = math.sin(jiggle_t * math.tau * 2.0) * 5.5 * damp
            scale *= 1.0 + 0.065 * math.sin(
                math.pi * min(1.0, jiggle_t * 1.5)) * damp

        p.translate(self.rect().center())
        p.scale(scale, scale)
        # 木鱼下面的小影子，跳起来时略缩小
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(0, 0, 0, 42)))
        p.drawEllipse(QRectF(-9, 13, 18, 3.5))

        p.save()
        p.translate(dx, dy)
        p.rotate(rot)
        if not self._fish.isNull():
            tw = 36.0
            th = tw * self._fish.height() / max(1, self._fish.width())
            p.drawPixmap(QRectF(-tw / 2, -th / 2 + 1.0, tw, th),
                         self._fish, QRectF(self._fish.rect()))
        p.restore()

        if self._alert:
            p.setBrush(QBrush(QColor("#ff453a")))
            p.setPen(QPen(QColor("#1c1c1e"), 1))
            p.drawEllipse(QPointF(self.width() / 2 - 5.5,
                                  -self.height() / 2 + 5.5), 3.4, 3.4)
        p.end()



# ---------- 会跳舞的小伙伴 ----------
class CompanionWindow(QWidget):
    """照片头 + 自绘身子的小人，会呼吸式摇摆/跳舞；提醒到点跳得更欢。"""

    list_requested = Signal()
    new_requested = Signal()
    photo_changed = Signal(bool, str)      # 成功?, 错误文案

    W = 132
    H = 168

    def __init__(self, profile: CompanionProfile, parent=None) -> None:
        super().__init__(parent)
        self.profile = profile
        self._pix: QPixmap | None = None
        self._head_pix: QPixmap | None = None
        self._phase = 0.0
        self._dance_until = 0.0
        self._alert = False
        self._drag_from: QPoint | None = None
        self._dragged = False
        self._born_at = 0.0
        self._visible_before_snip = False

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle("小二小伙伴")
        self.setFixedSize(self.W, self.H)
        self.setAcceptDrops(True)
        self.setToolTip("双击看提醒，右键更多")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._load_face()
        self._load_sprite()

    # ---- 照片 ----
    def _load_face(self) -> None:
        face = self.profile.face
        # 原图还在就用当前算法重做一遍，老版本存下的脸会自动升级
        if face and is_image_file(self.profile.source):
            try:
                prepare_face(self.profile.source, Path(face))
            except Exception:
                pass
        self._pix = QPixmap(face) if face else QPixmap()
        if self._pix.isNull():
            self._pix = None
        self._head_pix = round_face(self._pix, 56) if self._pix is not None else None

    def _load_sprite(self) -> None:
        """豆包生成的四帧整身小人；没有或跟当前照片对不上就回退到拼脸模式。"""
        self._frames: list[QPixmap] = []
        for path in self.profile.sprite_frames():
            pix = QPixmap(str(path))
            if pix.isNull():
                self._frames = []
                return
            self._frames.append(pix)

    def _paint_sprite(self, p: QPainter) -> None:
        """整身卡通：按节拍换姿势 + 上下弹跳、轻微摇摆。"""
        dancing = time.monotonic() < self._dance_until
        t = self._phase
        bob = math.sin(t * 2) * (5.0 if dancing else 2.0)
        sway = math.sin(t) * (4.0 if dancing else 1.6)
        cx = self.W / 2

        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        # 影子
        shadow_w = 62 - abs(bob) * 1.8
        p.setBrush(QBrush(QColor(0, 0, 0, 46)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(cx - shadow_w / 2, 158, shadow_w, 10))

        idx = int(t * 0.9) % 4 if dancing else 0
        pix = self._frames[idx]
        h = 150
        w = int(pix.width() * h / max(1, pix.height()))

        p.save()
        p.translate(cx, 161 + bob)
        p.rotate(sway)
        p.drawPixmap(int(-w / 2), -h, w, h, pix)
        p.restore()

        if self._alert:
            self._draw_bell(p, QPointF(cx + 30, 26), 0)
            p.setBrush(QBrush(QColor("#ff453a")))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx + 38, 19), 3.0, 3.0)

        age = time.monotonic() - self._born_at
        if age < 2.2:
            self._draw_sparkles(p, cx, 44 + bob)

    def has_face(self) -> bool:
        return self._pix is not None or bool(self._frames)

    def set_photo(self, src: str) -> tuple[bool, str]:
        """把用户拖进来的照片转成卡通脸；成功就显形并跳一下。"""
        if not is_image_file(src):
            return False, "这张图读不出来，换一张 png / jpg 试试"
        target = self.profile.face_target()
        if not prepare_face(src, target):
            return False, "这张图读不出来，换一张试试"
        self.profile.set_face(str(target), str(src))
        self._load_face()
        self._load_sprite()
        self._born_at = time.monotonic()
        self.show_companion()
        self.celebrate(seconds=3.5)
        self.photo_changed.emit(True, "")
        return True, ""

    def clear_photo(self) -> None:
        self.profile.clear_face()
        self._pix = None
        self._head_pix = None
        self._frames = []
        self.hide()

    # ---- 显示/隐藏 ----
    def _default_pos(self) -> QPoint:
        scr = QGuiApplication.primaryScreen()
        geo = scr.availableGeometry() if scr else QRect(0, 0, 1280, 720)
        return QPoint(geo.right() - self.W - 28, geo.bottom() - self.H - 24)

    def _place(self) -> None:
        pos = self.profile.pos
        if pos is None:
            self.move(self._default_pos())
            return
        x, y = pos
        scr = QGuiApplication.screenAt(QPoint(x + self.W // 2, y + self.H // 2))
        geo = scr.availableGeometry() if scr else QGuiApplication.primaryScreen().availableGeometry()
        x = max(geo.left() - self.W // 3, min(x, geo.right() - self.W * 2 // 3))
        y = max(geo.top() - 8, min(y, geo.bottom() - 24))
        self.move(x, y)

    def show_companion(self) -> bool:
        if not self.has_face():
            return False
        self._place()
        self.show()
        self.raise_()
        self._timer.start(33)
        self.profile.set_visible(True)
        return True

    def hide_companion(self, remember: bool = True) -> None:
        self._timer.stop()
        self.hide()
        if remember:
            self.profile.set_visible(False)

    def toggle(self) -> bool:
        if self.isVisible():
            self.hide_companion()
            return False
        return self.show_companion()

    def restore_if_enabled(self) -> bool:
        if not self.profile.visible or not self.has_face():
            return False
        return self.show_companion()

    def hide_for_snip(self) -> None:
        self._visible_before_snip = self.isVisible()
        self._timer.stop()
        self.hide()

    def restore_after_snip(self) -> None:
        if self._visible_before_snip and self.has_face():
            self.show()
            self.raise_()
            self._timer.start(33)
        self._visible_before_snip = False

    def anchor_rect(self) -> QRect:
        """提醒内容泡贴哪：小伙伴露着就贴它，否则返回空矩形让上层贴胶囊。"""
        if self.isVisible() and self.has_face():
            return self.geometry()
        return QRect()

    def set_alert(self, count: int) -> None:
        on = bool(count)
        if on != self._alert:
            self._alert = on
            self.update()

    def celebrate(self, seconds: float = 8.0) -> None:
        if not self.isVisible():
            return
        self._dance_until = time.monotonic() + max(1.0, seconds)
        self.raise_()
        if not self._timer.isActive():
            self._timer.start(33)

    # ---- 换照片 ----
    def pick_photo(self) -> None:
        start = str(Path.home() / "Pictures")
        name, _ = QFileDialog.getOpenFileName(
            None, "选一张照片", start, "图片 (*.png *.jpg *.jpeg *.bmp *.webp *.gif)")
        if name:
            ok, msg = self.set_photo(name)
            if not ok:
                QToolTip.showText(QCursor.pos(), msg)
                self.photo_changed.emit(False, msg)

    # ---- 动画 ----
    def _tick(self) -> None:
        dancing = time.monotonic() < self._dance_until
        self._phase += 0.13 if dancing else 0.045
        self.update()

    def _body_metrics(self) -> dict:
        t = self._phase
        dancing = time.monotonic() < self._dance_until
        bob = math.sin(t * 2) * (4.0 if dancing else 2.2)
        sway = math.sin(t) * (7.0 if dancing else 3.5)
        if dancing:
            # 跳舞时一只手向外抬起来挥，另一只手在身侧打拍子，远看就知道在跳
            arm_l = 18 + math.sin(t * 2) * 18
            arm_r = 62 + math.sin(t * 2 + math.pi) * 10
            elbow_r = 78 + math.sin(t * 2 + math.pi) * 18
            leg_l = max(0.0, math.sin(t * 2)) * 12
            leg_r = max(0.0, math.sin(t * 2 + math.pi)) * 12
            head_tilt = math.sin(t) * 7.5
        else:
            arm_l = 8 + math.sin(t * 2) * 7
            arm_r = 8 + math.sin(t * 2 + math.pi) * 7
            elbow_r = 10 + math.sin(t * 2 + math.pi) * 5
            leg_l = max(0.0, math.sin(t * 2)) * 5
            leg_r = max(0.0, math.sin(t * 2 + math.pi)) * 5
            head_tilt = math.sin(t) * 4.5
        return {
            "bob": bob,
            "sway": sway,
            "head_tilt": head_tilt,
            "arm_l": arm_l,
            "arm_r": arm_r,
            "elbow_r": elbow_r,
            "leg_l": leg_l,
            "leg_r": leg_r,
        }

    def paintEvent(self, _e) -> None:
        if self._frames:
            p = QPainter(self)
            self._paint_sprite(p)
            p.end()
            return
        m = self._body_metrics()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx = self.W / 2
        bob = m["bob"]

        # 地面影子（跳起来时缩小一点）
        shadow_w = 66 - abs(bob) * 2.5
        p.setBrush(QBrush(QColor(0, 0, 0, 46)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(cx - shadow_w / 2, 154, shadow_w, 11))

        hip_y = 120 + bob

        # 腿（先画，压在身子下面）
        for side, lift in ((-1, m["leg_l"]), (1, m["leg_r"])):
            p.save()
            p.translate(cx + side * 8, hip_y)
            p.rotate(-side * lift)      # 正值把小腿甩向左侧，左右反过来才是往外踢
            p.setBrush(QBrush(QColor("#2b2b30")))
            p.drawRoundedRect(QRectF(-3.9, 0, 7.8, 30), 3.9, 3.9)
            p.setBrush(QBrush(QColor("#1c1c1e")))
            p.drawRoundedRect(QRectF(-5.4, 27, 10.8, 6), 3, 3)
            p.restore()

        # 左手臂（身子后面那条）
        self._draw_arm(p, cx - 22.0, 84 + bob, m["arm_l"], 6)

        # 身子
        p.save()
        p.translate(cx, hip_y)
        p.rotate(m["sway"])
        body = QRectF(-18, -40, 36, 40)
        grad = QLinearGradient(0, -40, 0, 0)
        grad.setColorAt(0, QColor("#3b8dff"))
        grad.setColorAt(1, QColor("#4e56d6"))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(body, 14, 14)
        # 领口
        p.setBrush(QBrush(QColor(255, 255, 255, 225)))
        p.drawRoundedRect(QRectF(-6.5, -40, 13, 6), 3, 3)
        # 胸前的小灯
        p.setBrush(QBrush(QColor(255, 214, 10, 220)))
        p.drawEllipse(QPointF(0, -22), 2.6, 2.6)
        p.restore()

        # 脖子（补住头和身子之间的缝，不然像脑袋悬空）
        p.setBrush(QBrush(QColor("#f2c6a0")))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(cx - 5, 68 + bob, 10, 13), 4, 4)

        # 右手臂先画、头后画：举起来挥手时被头挡住一点，像从脖子后面抬上来
        self._draw_arm(p, cx + 22.0, 84 + bob, -m["arm_r"], -m["elbow_r"])

        # 头（照片/默认脸）
        p.save()
        p.translate(cx, 73 + bob)
        p.rotate(m["head_tilt"])
        p.translate(0, -27)
        head = QRectF(-27, -27, 54, 54)
        if self._head_pix is not None:
            p.drawPixmap(-28, -28, self._head_pix)
        else:
            p.setBrush(QBrush(QColor("#3a3a3f")))
            p.setPen(Qt.NoPen)
            p.drawEllipse(head)
            p.setBrush(QBrush(QColor("#e5e5ea")))
            p.drawEllipse(QPointF(-8, -3), 3, 3)
            p.drawEllipse(QPointF(8, -3), 3, 3)
            p.setPen(QPen(QColor("#e5e5ea"), 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(-7, 3, 14, 10), 200 * 16, 140 * 16)
        # 头外圈只给默认机器人脸用；照片是抠出来的，画圈会多一道白边
        if self._head_pix is None:
            p.setPen(QPen(QColor(255, 255, 255, 55), 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(head)
        p.restore()

        # 到点的小铃铛 + 红点
        if self._alert:
            self._draw_bell(p, QPointF(cx + 28, 24), bob)
            p.setBrush(QBrush(QColor("#ff453a")))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx + 36, 17), 3.0, 3.0)

        # 刚变身时闪一下星星，平时不撒
        age = time.monotonic() - self._born_at
        if age < 2.2:
            self._draw_sparkles(p, cx, 44 + bob)
        p.end()

    def _draw_arm(self, p: QPainter, x: float, y: float, angle: float, bend: float = 0.0) -> None:
        p.save()
        p.translate(x, y)
        p.rotate(angle)
        # 短袖 + 带肘的小臂 + 小手，比一整条蓝棍子像人
        p.setBrush(QBrush(QColor("#5b9dff")))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(-3.2, 0, 6.4, 10), 3.2, 3.2)
        p.translate(0, 9)
        p.rotate(bend)
        p.setBrush(QBrush(QColor("#f2c6a0")))
        p.drawRoundedRect(QRectF(-2.6, 0, 5.2, 12), 2.6, 2.6)
        p.drawEllipse(QPointF(0, 13), 3.2, 3.2)
        p.restore()

    def _draw_bell(self, p: QPainter, c: QPointF, bob: float) -> None:
        p.save()
        p.translate(c.x(), c.y() + bob)
        p.setPen(QPen(QColor("#ffd60a"), 1.6))
        p.setBrush(QBrush(QColor("#ffd60a")))
        path = QPainterPath()
        path.moveTo(-5, 3)
        path.cubicTo(-3.5, 0.5, -3.5, -6, 0, -6.5)
        path.cubicTo(3.5, -6, 3.5, 0.5, 5, 3)
        p.drawPath(path)
        p.drawLine(QPointF(-6, 3), QPointF(6, 3))
        p.drawEllipse(QPointF(0, 5), 1.4, 1.4)
        p.restore()

    def _draw_sparkles(self, p: QPainter, cx: float, cy: float) -> None:
        t = self._phase
        for i in range(4):
            ang = t * 0.8 + i * math.pi / 2
            r = 38 + math.sin(t * 2 + i) * 4
            x = cx + math.cos(ang) * r
            y = cy + math.sin(ang) * r * 0.9
            alpha = 150 + int(80 * math.sin(t * 3 + i * 1.7))
            alpha = max(0, min(255, alpha))
            p.setBrush(QBrush(QColor(255, 214, 10, alpha)))
            p.setPen(Qt.NoPen)
            size = 2.6 if i % 2 else 3.4
            p.drawPolygon(QPolygonF([
                QPointF(x, y - size), QPointF(x + size * 0.6, y),
                QPointF(x, y + size), QPointF(x - size * 0.6, y),
            ]))

    # ---- 鼠标交互 ----
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragged = False
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            self._dragged = True
            self.move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self._dragged:
                self.profile.set_pos(self.x(), self.y())
            self._drag_from = None
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and not self._dragged:
            self.list_requested.emit()
            event.accept()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#1c1c1e;color:#f2f2f7;border:1px solid #3a3a3c;"
            "border-radius:8px;padding:5px;}"
            "QMenu::item{padding:6px 18px;border-radius:5px;}"
            "QMenu::item:selected{background:#3a3a3c;}")
        act_photo = menu.addAction("换一张照片…")
        act_new = menu.addAction("新建提醒")
        act_list = menu.addAction("提醒列表")
        menu.addSeparator()
        act_hide = menu.addAction("让小伙伴歇会儿")
        act_photo.triggered.connect(self.pick_photo)
        act_new.triggered.connect(self.new_requested.emit)
        act_list.triggered.connect(self.list_requested.emit)
        act_hide.triggered.connect(self.hide_companion)
        menu.exec(event.globalPos())

    # ---- 拖照片直接换 ----
    def dragEnterEvent(self, event) -> None:
        if any(is_image_file(u.toLocalFile()) for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for u in event.mimeData().urls():
            path = u.toLocalFile()
            if is_image_file(path):
                event.acceptProposedAction()
                ok, msg = self.set_photo(path)
                if not ok:
                    QToolTip.showText(QCursor.pos(), msg)
                return
        event.ignore()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(33)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)
