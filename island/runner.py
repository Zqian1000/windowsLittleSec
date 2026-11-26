"""胶囊里的隐藏小游戏：木鱼跑酷。

彩蛋触发条件在 ``AgentAvatarButton``：1.2 秒内连点木鱼 20 次。
窗口宽度跟当前胶囊一致，高度约为胶囊的八成，保证画面比例是原本就按
这个高度画的，而不是把一条扁图硬压进去：
- 白底、灰色虚线地面、像素仙人掌；
- 角色是黑白简易小人，尺寸比木鱼小，跳起来和障碍之间留得出余量；
- 点击 / 空格 / ↑ 起跳，距离越远跑得越快；
- 撞上就停，点击 / 空格重来，Esc 关闭。
"""
from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class RunnerGame(QWidget):
    """胶囊同宽、约八成高的长条跑酷窗。"""

    closed = Signal()

    GRAVITY = 520.0
    START_SPEED = 80.0
    MAX_SPEED = 300.0

    def __init__(self, width: int, height: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(max(150, int(width)), max(44, int(height)))
        self.setFocusPolicy(Qt.StrongFocus)
        self.setWindowTitle("小二 · 木鱼跑酷")

        self._rng = random.Random()

        # 按窗口高度确定角色、障碍和跳跃参数，避免把大画面压进小窗口
        h = self.height()
        self._ground = h - max(3, int(round(h * 0.07)))
        # 小人只占窗口约三分之一高度，跳起来才有明显的躲避余量
        self._char_h = max(14, int(round(h * 0.37)))
        self._char_w = max(9, int(round(self._char_h * 0.62)))
        self._char_x = max(10, int(round(h * 0.23)))
        self._jump_peak = h * 0.44
        self._jump_v = math.sqrt(2.0 * self.GRAVITY * self._jump_peak)

        self._obstacles: list[dict] = []
        self._last_tick = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

        self._state = "run"          # run / dead
        self._distance = 0.0
        self._speed = self.START_SPEED
        self._spawn_in = 0.9
        self._jump_h = 0.0
        self._vy = 0.0
        self._score = 0
        self._best = 0
        self._dead_at = 0.0
        self._dust = 0.0

    # ---- 生命周期 ----
    def show_game(self) -> None:
        self._reset()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._timer.start()

    def _reset(self) -> None:
        self._obstacles.clear()
        self._state = "run"
        self._distance = 0.0
        self._speed = self.START_SPEED
        self._spawn_in = 0.9
        self._jump_h = 0.0
        self._vy = 0.0
        self._score = 0
        self._dead_at = 0.0
        self._last_tick = time.monotonic()
        self.update()

    def restart(self) -> None:
        self._reset()
        self._timer.start()
        self.setFocus()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._best = max(self._best, self._score)
        self.closed.emit()
        super().closeEvent(event)

    # ---- 输入 ----
    def _jump(self) -> None:
        if self._state == "dead":
            self.restart()
            return
        if self._jump_h <= 0.01 and self._vy <= 0.0:
            self._vy = self._jump_v

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._jump()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            self.close()
            return
        if key in (Qt.Key_Space, Qt.Key_Up, Qt.Key_W):
            if self._state == "dead":
                self.restart()
            else:
                self._jump()
            event.accept()
            return
        super().keyPressEvent(event)

    # ---- 每帧 ----
    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.045, now - self._last_tick)
        self._last_tick = now
        if self._state != "run":
            self.update()
            return

        self._speed = min(self.MAX_SPEED,
                          self.START_SPEED + self._distance * 0.05)
        self._distance += self._speed * dt
        self._score = int(self._distance // 9)

        # 跳跃
        if self._jump_h > 0.0 or self._vy > 0.0:
            self._vy -= self.GRAVITY * dt
            self._jump_h += self._vy * dt
            if self._jump_h <= 0.0:
                self._jump_h = 0.0
                self._vy = 0.0

        # 障碍
        self._spawn_in -= dt
        if self._spawn_in <= 0.0:
            self._spawn()

        for obs in self._obstacles:
            obs["x"] -= self._speed * dt
        self._obstacles = [o for o in self._obstacles if o["x"] + o["w"] > -30]

        if self._hit_test():
            self._state = "dead"
            self._dead_at = now
            self._best = max(self._best, self._score)
            self.update()
            return

        self._dust = (self._dust + self._speed * dt) % 22.0
        self.update()

    def _spawn(self) -> None:
        h_scale = self.height()
        h = float(self._rng.randint(max(9, int(round(h_scale * 0.20))),
                                    max(11, int(round(h_scale * 0.32)))))
        w = float(self._rng.randint(max(5, int(round(h_scale * 0.08))),
                                    max(6, int(round(h_scale * 0.13)))))
        if self._rng.random() < 0.22:
            w = w * 2.0 + 3.0
        self._obstacles.append({"x": self.width() + 8.0, "w": w, "h": h})
        # 速度越快，间隔按比例拉长，给起跳留反应时间
        gap = self._rng.uniform(118.0, 176.0) + self._speed * 0.55
        self._spawn_in = gap / self._speed

    def _hit_test(self) -> bool:
        cx = float(self._char_x)
        cw = float(self._char_w - 4)
        for obs in self._obstacles:
            if cx + cw <= obs["x"] + 1.0 or cx >= obs["x"] + obs["w"] - 1.0:
                continue
            if self._jump_h + 1.0 < obs["h"]:
                return True
        return False

    # ---- 绘制 ----
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1,
                                   self.height() - 1),
                            self.height() / 2.0, self.height() / 2.0)
        p.setClipPath(path)
        p.fillPath(path, QColor("#f8f8f8"))
        p.setPen(QPen(QColor("#d0d0d0"), 1))
        p.drawPath(path)

        g = float(self._ground)
        # 地面虚线
        dash = max(9.0, self.height() * 0.16)
        gap = max(7.0, self.height() * 0.12)
        offset = self._distance % (dash + gap)
        p.setPen(QPen(QColor("#a8a8a8"), 1))
        x = -offset
        while x < self.width():
            p.drawLine(QPointF(x, g), QPointF(x + dash, g))
            x += dash + gap

        # 仙人掌
        for obs in self._obstacles:
            self._draw_cactus(p, obs, g)

        # 黑白简易小人
        top = g - self._char_h - self._jump_h
        self._draw_runner(p, top)
        # 跑动时脚下的小尘土
        if self._state == "run":
            p.setPen(QPen(QColor("#c8c8c8"), 1))
            p.drawLine(QPointF(self._char_x, g + 2),
                       QPointF(self._char_x + 7 - self._dust / 6, g + 2))

        # 分数
        p.setPen(QColor("#5a5a5a"))
        font = QFont("Consolas")
        font.setPixelSize(max(10, int(round(self.height() * 0.19))))
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRectF(0, 4, self.width() - 9, 15),
                   Qt.AlignRight | Qt.AlignVCenter, f"{self._score:05d}")

        if self._state == "dead":
            p.fillRect(self.rect(), QColor(248, 248, 248, 210))
            p.setPen(QColor("#454545"))
            font.setPixelSize(max(11, int(round(self.height() * 0.21))))
            p.setFont(font)
            p.drawText(self.rect(), Qt.AlignCenter, "撞了 · 点击 / 空格重来")
        p.end()

    def _draw_runner(self, p: QPainter, top: float) -> None:
        """黑白火柴人：白脸黑边、黑身体和四肢，随跑动摆腿。"""
        u = self._char_h / 22.0
        cx = self._char_x + self._char_w / 2.0
        ink = QColor("#1d1d1d")

        head_cy = top + 4.2 * u
        head_r = 3.5 * u
        p.setPen(QPen(ink, max(1.0, 1.4 * u)))
        p.setBrush(QColor("#fbfbfb"))
        p.drawEllipse(QPointF(cx, head_cy), head_r, head_r)

        # 眼睛：一个黑点，黑白对比就出来了
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        p.drawEllipse(QPointF(cx + 1.0 * u, head_cy - 0.4 * u),
                      0.55 * u, 0.55 * u)

        body_top = top + 7.6 * u
        hip = top + 14.4 * u
        # 黑色小身板，比一条线更像“人”
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        p.drawRoundedRect(QRectF(cx - 2.1 * u, body_top,
                                 4.2 * u, hip - body_top), 1.4 * u, 1.4 * u)

        # 手臂：跑动时前后摆，跳起来时收一点
        airborne = self._jump_h > 0.01
        swing = 0.0 if airborne else math.sin(self._distance * 0.20) * 1.6 * u
        arm_y = top + 9.8 * u
        p.setPen(QPen(ink, max(1.0, 1.35 * u)))
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(cx, arm_y),
                   QPointF(cx - 4.2 * u, arm_y + 2.0 * u - swing))
        p.drawLine(QPointF(cx, arm_y),
                   QPointF(cx + 4.2 * u, arm_y + 2.0 * u + swing))

        # 腿：落地时交替迈步，腾空时收腿，脚下的余量更直观
        p.setPen(QPen(ink, max(1.1, 1.6 * u)))
        if airborne:
            p.drawLine(QPointF(cx, hip), QPointF(cx - 2.1 * u, top + 19.2 * u))
            p.drawLine(QPointF(cx, hip), QPointF(cx + 2.1 * u, top + 19.2 * u))
        else:
            step = math.sin(self._distance * 0.20) * 1.5 * u
            p.drawLine(QPointF(cx, hip),
                       QPointF(cx - 2.4 * u - step, top + 21.0 * u))
            p.drawLine(QPointF(cx, hip),
                       QPointF(cx + 2.4 * u + step, top + 21.0 * u))

    @staticmethod
    def _draw_cactus(p: QPainter, obs: dict, ground: float) -> None:
        x = int(round(obs["x"]))
        w = max(5, int(round(obs["w"])))
        h = max(7, int(round(obs["h"])))
        base = int(ground)
        dark = QColor("#535353")
        p.fillRect(x, base - h, w, h, dark)
        arm_w = max(2, int(round(w * 0.26)))
        arm_h = max(3, int(round(h * 0.38)))
        # 左右各伸一只像素小胳膊，轮廓更像 Chrome 断网图里的仙人掌
        p.fillRect(x - arm_w - 1, base - h + max(2, h // 3), arm_w, arm_h,
                   dark)
        p.fillRect(x + w + 1, base - h + max(2, h // 4), arm_w, arm_h,
                   dark)
        p.fillRect(x - arm_w - 1, base - h + max(2, h // 3), arm_w + 2, 2,
                   dark)
        p.fillRect(x + w - 1, base - h + max(2, h // 4), arm_w + 2, 2,
                   dark)
