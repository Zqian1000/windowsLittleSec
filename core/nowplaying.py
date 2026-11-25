"""没有系统媒体会话时的兜底：读播放器窗口标题拿歌名/歌手，再从网易云接口取封面。

Windows 的 SMTC 只有客户端主动上报时才存在。本机实测网易云（含正在播放时）
`get_sessions()` 返回 0，因此岛里过去只能显示播放器列表。网易云会把当前曲目
写进主窗口标题（"歌名 - 歌手"，窗口缩在托盘里也照样更新），这里就按标题取。

封面：用歌词接口同一套网易云搜索（歌名 + 歌手 → 专辑 picUrl）下载，带磁盘缓存。
位置/时长：标题里没有，交给 core/progress.py 按切歌与播放状态推算。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    import win32gui
    import win32process
    import psutil
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False

TITLE_SEP = " - "


@dataclass(frozen=True)
class PlayerSpec:
    key: str
    name: str
    exe: str
    classes: Tuple[str, ...]      # 会带曲目标题的窗口类名
    skip_titles: Tuple[str, ...] = ()   # 不带曲目、纯功能窗的标题


PLAYERS: List[PlayerSpec] = [
    PlayerSpec("cloudmusic", "网易云音乐", "cloudmusic.exe",
               ("OrpheusBrowserHost", "MiniPlayer"),
               skip_titles=("迷你播放器",)),
    PlayerSpec("qqmusic", "QQ音乐", "qqmusic.exe",
               ("OrpheusBrowserHost", "TXMiniWnd", "QQMusic_Daemon_Wnd")),
    PlayerSpec("kugou", "酷狗音乐", "kugou.exe",
               ("KugouMainWnd", "KuGouMiniPlayer")),
]


def _split_title(text: str) -> Optional[Tuple[str, str]]:
    text = (text or "").strip()
    if TITLE_SEP not in text:
        return None
    head, tail = text.rsplit(TITLE_SEP, 1)
    head, tail = head.strip(), tail.strip()
    if not head or not tail:
        return None
    return head, tail


def read_now_playing() -> Optional[Tuple[PlayerSpec, str, str, int]]:
    """返回 (播放器, 歌名, 歌手, 窗口句柄)；没有任何播放器在报曲目就返回 None。"""
    if not _HAS_WIN32:
        return None
    pids = {}
    by_exe = {spec.exe: spec for spec in PLAYERS}
    try:
        for p in psutil.process_iter(["pid", "name"]):
            name = (p.info["name"] or "").lower()
            spec = by_exe.get(name)
            if spec is not None:
                pids[p.info["pid"]] = spec
    except Exception:
        return None
    if not pids:
        return None

    found: List[Tuple[PlayerSpec, str, str, int]] = []

    def _enum(hwnd, _):
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            spec = pids.get(pid)
            if spec is None:
                return True
            if win32gui.GetClassName(hwnd) not in spec.classes:
                return True
            title = win32gui.GetWindowText(hwnd)
            if title.strip() in spec.skip_titles:
                return True
            pair = _split_title(title)
            if pair is None:
                return True
            found.append((spec, pair[0], pair[1], hwnd))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_enum, None)
    return found[0] if found else None


class NowPlayingFallback:
    """标题兜底源：0.6 秒缓存一次枚举结果（切歌要跟得上），封面异步取。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cover_cache: dict = {}
        self._pending: dict = {}
        self._cached = None
        self._cached_at = 0.0

    def read(self):
        """返回 (spec, 歌名, 歌手, hwnd)；顺带把封面塞进 cover()。"""
        import time
        now = time.monotonic()
        with self._lock:
            if self._cached is not None and now - self._cached_at < 0.6:
                return self._cached
        info = read_now_playing()
        with self._lock:
            self._cached = info
            self._cached_at = now
        if info:
            self._kick_cover(info[1], info[2])
        return info

    def _kick_cover(self, title: str, artist: str) -> None:
        key = f"{title}|{artist}"
        if key in self._cover_cache or key in self._pending:
            return
        self._pending[key] = True
        threading.Thread(target=self._fetch_cover, args=(key, title, artist),
                         daemon=True).start()

    def _fetch_cover(self, key: str, title: str, artist: str) -> None:
        try:
            from core import lyrics
            data = lyrics.fetch_cover(title, artist)
            if data:
                self._cover_cache[key] = data
        except Exception:
            pass
        finally:
            self._pending.pop(key, None)

    def cover(self, title: str, artist: str) -> bytes:
        return self._cover_cache.get(f"{title}|{artist}", b"")
