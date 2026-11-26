"""所有贴岛浮层的统一位置管理。

卡片的定位不再各写一套 ``anchor.bottom()+8``。统一交给 ``PopupLayout``：

- 锚点（胶囊）先夹回当前屏幕，避免自动藏到上沿时 anchor 变负数；
- 候选位置按“下 / 上 / 右 / 左”依次尝试，并计算与胶囊、其他可见卡片的
  重叠面积，选遮挡最少且仍在屏幕内的一处；
- 新卡片只要在 ``popup_near`` 里调用 ``place_popup(self, anchor)`` 就自动
  纳入管理，不需要再往 bar 里加特判。
"""
from __future__ import annotations

import weakref

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

EDGE = 6      # 离屏幕边缘的最小间距
GAP = 8       # 和胶囊 / 其他卡片之间的间距


class PopupLayout:
    """进程内共享的浮层位置表，谁调用 place 谁自动登记。"""

    _shared: "PopupLayout | None" = None

    def __init__(self) -> None:
        self._widgets: list[weakref.ReferenceType] = []

    @classmethod
    def shared(cls) -> "PopupLayout":
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    # ---- 登记 ----
    def register(self, widget: QWidget) -> None:
        self._prune()
        if any(ref() is widget for ref in self._widgets):
            return
        try:
            self._widgets.append(weakref.ref(widget))
        except TypeError:
            pass

    def _prune(self) -> None:
        alive = []
        for ref in self._widgets:
            obj = ref()
            if obj is None:
                continue
            try:
                obj.objectName()
            except RuntimeError:
                continue
            alive.append(ref)
        self._widgets = alive

    def _others(self, widget: QWidget) -> list[QRect]:
        self._prune()
        out = []
        for ref in self._widgets:
            obj = ref()
            if obj is None or obj is widget:
                continue
            try:
                if obj.isVisible():
                    out.append(obj.frameGeometry())
            except RuntimeError:
                continue
        return out

    # ---- 摆放 ----
    @staticmethod
    def _screen_for(anchor: QRect):
        screen = None
        if anchor is not None and anchor.isValid():
            screen = QGuiApplication.screenAt(anchor.center())
        return screen or QGuiApplication.primaryScreen()

    @staticmethod
    def _visible_anchor(anchor: QRect, avail: QRect) -> QRect:
        """把可能在屏幕上沿外的胶囊锚点拉回可见区。

        胶囊靠边隐藏时 geometry 可能是 y=-70；直接拿它当锚点算出来的
        气泡会压到胶囊展开后的位置，甚至在屏幕外。
        """
        if anchor is None or not anchor.isValid() or anchor.width() <= 0:
            return QRect(avail.center().x(), avail.top() + EDGE, 1, 1)
        rect = QRect(anchor)
        if rect.bottom() < avail.top() + EDGE:
            rect.moveTop(avail.top() + EDGE)
        elif rect.top() > avail.bottom() - EDGE:
            rect.moveBottom(avail.bottom() - EDGE)
        if rect.right() < avail.left() + EDGE:
            rect.moveLeft(avail.left() + EDGE)
        elif rect.left() > avail.right() - EDGE:
            rect.moveRight(avail.right() - EDGE)
        return rect

    @staticmethod
    def _overlap(a: QRect, b: QRect) -> int:
        hit = a.intersected(b)
        if hit.isEmpty():
            return 0
        return hit.width() * hit.height()

    def place(self, widget: QWidget, anchor: QRect, *,
              gap: int = GAP, preferred: tuple[str, ...] | None = None) -> QPoint:
        """把 widget 放到 anchor 附近，避开 anchor 和其他可见浮层。"""
        self.register(widget)
        screen = self._screen_for(anchor)
        if screen is None:
            return widget.pos()
        avail = screen.availableGeometry()
        a = self._visible_anchor(anchor, avail)

        w = max(1, widget.width())
        h = max(1, widget.height())
        cx = a.center().x()
        cy = a.center().y()
        left = a.left()
        right = a.right() + 1
        top = a.top()
        bottom = a.bottom() + 1

        # 候选位置：优先下方，其次纵向接在已有浮层下面，再考虑上方/两侧。
        raw = {
            "below": QRect(cx - w // 2, bottom + gap, w, h),
            "above": QRect(cx - w // 2, top - gap - h, w, h),
            "right": QRect(right + gap, top, w, h),
            "left": QRect(left - gap - w, top, w, h),
            "below-left": QRect(left, bottom + gap, w, h),
            "below-right": QRect(right - w, bottom + gap, w, h),
        }
        others = self._others(widget)
        for i, other in enumerate(others):
            raw[f"stack-below-{i}"] = QRect(cx - w // 2, other.bottom() + gap, w, h)
            raw[f"stack-above-{i}"] = QRect(cx - w // 2, other.top() - gap - h, w, h)
            raw[f"right-of-{i}"] = QRect(other.right() + gap, other.top(), w, h)
            raw[f"left-of-{i}"] = QRect(other.left() - gap - w, other.top(), w, h)

        default = (["below"]
                   + [name for name in raw if name.startswith("stack-below-")]
                   + ["above", "right", "left"])
        order = list(preferred or default)
        order += [name for name in default if name not in order]
        order += [name for name in raw if name not in order]
        blocked = [a] + others

        best: tuple[int, int, QRect] | None = None
        for idx, name in enumerate(order):
            if name not in raw:
                continue
            candidate = raw[name]
            # 夹回屏幕；留 6px 边距，避免贴着任务栏/菜单栏。
            x = max(avail.left() + EDGE,
                    min(candidate.x(), avail.right() - w - EDGE + 1))
            y = max(avail.top() + EDGE,
                    min(candidate.y(), avail.bottom() - h - EDGE + 1))
            rect = QRect(x, y, w, h)
            score = sum(self._overlap(rect, b) for b in blocked)
            # 同分时保持首选方向；右侧/左侧本来就会和胶囊同一水平线，
            # 给一点点惩罚，只有在下方确实放不下时才翻过去。
            tie = idx * 4
            if best is None or (score, tie) < (best[0], best[1]):
                best = (score, tie, rect)
        if best is None:
            return widget.pos()
        rect = best[2]
        widget.move(rect.topLeft())
        return rect.topLeft()


def place_popup(widget: QWidget, anchor: QRect, *,
                gap: int = GAP, preferred: tuple[str, ...] | None = None) -> QPoint:
    """模块级入口，给各卡片的 popup_near 直接调用。"""
    return PopupLayout.shared().place(widget, anchor, gap=gap, preferred=preferred)
