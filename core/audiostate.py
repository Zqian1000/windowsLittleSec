"""用 CoreAudio 的会话峰值判断"当前到底在不在放声音"。

网易云不上报 Windows 媒体会话时，标题兜底只能拿到歌名/歌手，拿不到播放
状态——这就是过去"岛里一直显示播放中、播放暂停与实际总有时差"的原因。
但声音本身一直在走 WASAPI：播放时该进程的会话峰值 > 0，暂停后约一秒掉到 0。
据此给标题兜底补上真实的播放状态（约 1 秒内跟得上）。

只读本机音频会话的音量峰值，不录制、不分析声音内容，不联网。

注意静音：系统或播放器被静音时峰值恒为 0，此时"没声音"并不等于"已暂停"，
所以这种情况返回"不知道"（None），界面保留上一次的判断，别把正在放的歌当暂停。
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional

try:
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    _HAS_PYCAW = True
except Exception:                        # 依赖缺失时整体降级为"不知道"
    _HAS_PYCAW = False

PEAK_EPS = 0.002          # 低于这个值按静音处理
SILENT_FOR_PAUSE = 0.5    # 连续静音超过 0.5 秒判暂停（安静段落靠这个缓冲不来回跳）
INACTIVE_FOR_PAUSE = 1.2  # 会话失效要持续这么久才认：切歌/缓冲的瞬间它会短暂归零


def _peak_of(pid: int) -> Optional[float]:
    """该进程所有音频会话里最大的峰值；读不到返回 None。"""
    best: Optional[float] = None
    try:
        for session in AudioUtilities.GetAllSessions():
            try:
                proc = session.Process
                if proc is None or proc.pid != pid:
                    continue
                # pycaw 的 AudioSession 是包装对象，峰值要从它持有的会话控制接口拿
                ctl = getattr(session, "_ctl", session)
                peak = float(ctl.QueryInterface(IAudioMeterInformation).GetPeakValue())
            except Exception:
                continue
            best = peak if best is None else max(best, peak)
    except Exception:
        return None
    return best


def _state_of(pid: int) -> Optional[int]:
    """该进程音频会话的状态：0=Inactive（不在出声）、1=Active、2=Expired。

    比峰值更可靠的"暂停"证据：实测网易云暂停后会话直接变 Inactive，
    而播放时（哪怕被静音）会话仍是 Active。
    """
    try:
        for session in AudioUtilities.GetAllSessions():
            proc = session.Process
            if proc is None or proc.pid != pid:
                continue
            ctl = getattr(session, "_ctl", session)
            return int(ctl.GetState())
    except Exception:
        return None
    return None


class PlaybackProbe:
    """按进程判断是否正在出声；结果缓存一小会儿，避免界面每次刷新都枚举会话。"""

    def __init__(self, ttl: float = 0.25) -> None:
        self._lock = threading.Lock()
        self._ttl = ttl
        self._cache: Optional[tuple] = None          # (pid, playing, at)
        self._last_loud: Dict[int, float] = {}
        self._inactive_since: Dict[int, float] = {}
        self._went_paused: Dict[int, bool] = {}
        self._mute_cache: Optional[tuple] = None     # (pid, muted, at)

    def is_playing(self, pid: int) -> Optional[bool]:
        """True 在放、False 已暂停、None 读不到（调用方保留原判断）。"""
        if not _HAS_PYCAW or not pid:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache
            if cached and cached[0] == pid and now - cached[2] < self._ttl:
                return cached[1]

        # 1) 会话状态：Inactive 说明音频流停了，但要持续一会儿才认——切歌/缓冲
        #    的那一两百毫秒里它也会短暂归零，认早了就会出现"点了下一首显示未在播放"
        state = _state_of(pid)
        if state == 0:
            since = self._inactive_since.setdefault(pid, now)
            if now - since < INACTIVE_FOR_PAUSE:
                with self._lock:
                    self._cache = (pid, None, now)
                return None
            self._last_loud.pop(pid, None)
            self._went_paused[pid] = True
            with self._lock:
                self._cache = (pid, False, now)
            return False
        self._inactive_since.pop(pid, None)
        if self._went_paused.pop(pid, False):
            # 刚确认过暂停、现在会话又活了：这是在恢复了，静音也一样能判
            with self._lock:
                self._cache = (pid, True, now)
            return True
        # 2) 被静音时峰值恒 0，判不出播放/暂停，交给调用方找别的证据（进度线动不动）
        if self._muted(pid):
            with self._lock:
                self._cache = (pid, None, now)
            return None

        peak = _peak_of(pid)
        if peak is None:
            return None
        if peak > PEAK_EPS:
            self._last_loud[pid] = now
            playing = True
        else:
            playing = (now - self._last_loud.get(pid, 0.0)) < SILENT_FOR_PAUSE
        with self._lock:
            self._cache = (pid, playing, now)
        return playing

    def _muted(self, pid: int) -> bool:
        """系统输出或该播放器自己被静音（2 秒缓存）。"""
        now = time.monotonic()
        if self._mute_cache and self._mute_cache[0] == pid \
                and now - self._mute_cache[2] < 2.0:
            return self._mute_cache[1]
        muted = False
        try:
            from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
            speakers = AudioUtilities.GetSpeakers()
            if speakers is not None and speakers.EndpointVolume.GetMute():
                muted = True
            for session in AudioUtilities.GetAllSessions():
                proc = session.Process
                if proc is None or proc.pid != pid or muted:
                    continue
                ctl = getattr(session, "_ctl", session)
                if ctl.QueryInterface(ISimpleAudioVolume).GetMute():
                    muted = True
        except Exception:
            return False
        self._mute_cache = (pid, muted, now)
        return muted

    def forget(self, pid: int) -> None:
        self._last_loud.pop(pid, None)
        self._inactive_since.pop(pid, None)
        self._went_paused.pop(pid, None)
        with self._lock:
            if self._cache and self._cache[0] == pid:
                self._cache = None

