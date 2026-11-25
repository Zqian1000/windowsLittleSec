"""刷新节流：把定时器包一层，按实测耗时自动放宽间隔。

低配机器 / 装了会 hook 屏幕抓取的安全软件时，单次刷新可能从十几毫秒涨到
上百毫秒；固定节拍（比如红点检测每 0.3 秒）会把 CPU 吃满、界面跟着发飘。
这里不猜硬件，直接看每次回调花了多久：超预算就逐步拉长自己的间隔，
连续跑得很快再慢慢缩回去，最低不低于原始节拍。
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QTimer


class PacedTimer(QTimer):
    """带自适应间隔的 QTimer。"""

    def __init__(self, base_ms: int, max_ms: int, slot: Callable[[], None],
                 name: str = "", parent=None) -> None:
        super().__init__(parent)
        self._base = max(1, int(base_ms))
        self._max = max(self._base, int(max_ms))
        self._slot = slot
        self._name = name
        self._fast_ticks = 0
        self._slow_ticks = 0
        self.last_cost_ms = 0.0
        self.max_cost_ms = 0.0
        self.timeout.connect(self._tick)
        self.setInterval(self._base)

    # ---- 内部 ----

    def _tick(self) -> None:
        t0 = time.perf_counter()
        try:
            self._slot()
        finally:
            cost = (time.perf_counter() - t0) * 1000.0
            self.last_cost_ms = cost
            if cost > self.max_cost_ms:
                self.max_cost_ms = cost
            self._adjust(cost)

    def _adjust(self, cost: float) -> None:
        interval = self.interval()
        budget = self._base * 0.4          # 一次回调不该吃掉节拍的 40%
        cheap = self._base * 0.12

        if cost > budget:
            self._slow_ticks += 1
            self._fast_ticks = 0
            # 连续两次超预算才动，避免偶发一次抖动就降频
            if self._slow_ticks >= 2:
                nxt = min(self._max, int(interval * 1.6) + 20)
                if nxt > interval:
                    self.setInterval(nxt)
                self._slow_ticks = 0
            return

        self._slow_ticks = 0
        if cost < cheap and interval > self._base:
            self._fast_ticks += 1
            if self._fast_ticks >= 8:      # 连续 8 次都很轻，才往回收
                nxt = max(self._base, int(interval * 0.8))
                self.setInterval(nxt)
                self._fast_ticks = 0
        else:
            self._fast_ticks = 0

    # ---- 只读属性 ----

    @property
    def name(self) -> str:
        return self._name

    @property
    def base_interval(self) -> int:
        return self._base

    def stats(self) -> str:
        return (f"{self._name}: 间隔 {self.interval()}ms(基准 {self._base}ms) "
                f"最近 {self.last_cost_ms:.0f}ms 峰值 {self.max_cost_ms:.0f}ms")


def start_paced(timers: list) -> None:
    for t in timers:
        t.start(t.interval())


def slowest(timers: list) -> Optional[PacedTimer]:
    if not timers:
        return None
    return max(timers, key=lambda t: t.max_cost_ms)
