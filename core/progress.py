"""播放进度兜底：没有系统时间轴时，按"曲目切换 / 播放暂停"打锚点推算进度。

为什么需要它：网易云桌面版在这台机器上不注册 SMTC 会话，拿不到
`timeline.position`，歌词条曾因此整条不显示（一没有进度就收起）。
标题（"歌名 - 歌手"）能实时反映切歌，播放/暂停也能用音频峰值判定，
于是进度可以这样推：

* 看到一首新歌 → 认为它从头开始放，锚点归零；
* 播放中 → 进度 = 锚点 + 流逝时间；
* 暂停 → 冻在当前值；再播 → 用冻住的值重新打锚点；
* 拖动进度条会失配，等接入真实进度源后调 `seek()` 校正。

这是"够用的近似"，不是权威时间轴；拿到 SMTC / 注入式时间轴后，
把真实进度 `seek()` 进来即可，界面逻辑不用动。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PlayClock:
    """一首歌的播放时钟：一个已知进度点 + 对应时刻。"""

    key: str = ""
    position: float = 0.0      # 锚点上的进度（秒）
    anchor: float = 0.0        # 锚点对应的 time.monotonic()
    playing: bool = False
    duration: float = 0.0      # 曲长（秒），0 表示未知

    def at(self, now: float) -> float:
        """推算 now 时刻的进度。"""
        pos = self.position
        if self.playing and self.anchor:
            pos += now - self.anchor
        if self.duration:
            pos = min(pos, self.duration)
        return max(0.0, pos)

    def _reseat(self, pos: float, playing: bool, now: float) -> None:
        """把锚点挪到 pos/now，之后的推算都从这里长出去。"""
        self.position = max(0.0, pos)
        self.anchor = now
        self.playing = playing


class ProgressEstimator:
    """把 (曲目标识, 是否在播) 变成秒级进度；同一首歌内状态跳变只打锚点。"""

    def __init__(self, start_offset: float = 0.0) -> None:
        self._clock = PlayClock()
        self._start_offset = start_offset   # 首次见到某首歌时的初始猜测

    @property
    def clock(self) -> PlayClock:
        return self._clock

    def update(self, key: str, playing: bool, now: Optional[float] = None) -> float:
        now = time.monotonic() if now is None else now
        clock = self._clock
        if key and key != clock.key:
            # 换歌了：从头开始（岛启动时已在播的歌只能从 0 猜）
            clock.key = key
            clock.duration = 0.0
            clock._reseat(self._start_offset, playing, now)
            return clock.at(now)
        if playing != clock.playing:
            clock._reseat(clock.at(now), playing, now)   # 暂停冻结 / 恢复续算
        return clock.at(now)

    def seek(self, position: float, duration: float = 0.0,
             now: Optional[float] = None) -> float:
        """真实进度源（SMTC / 注入式时间轴 / 拖动条）校正时调用。"""
        now = time.monotonic() if now is None else now
        clock = self._clock
        clock.duration = duration or clock.duration
        clock._reseat(position, clock.playing, now)
        return clock.at(now)

    def forget(self) -> None:
        self._clock = PlayClock()
