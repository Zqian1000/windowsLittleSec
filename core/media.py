"""本机媒体会话（Windows SMTC）读取与播放控制。

优先用 winsdk 读取系统媒体会话的曲目/状态并控制；winsdk 不可用时
退化为发送系统媒体键。全程只读本机当前会话，不联网。
"""
from __future__ import annotations

import asyncio
import ctypes
import threading
from dataclasses import dataclass
from typing import Optional

# 虚拟媒体键
VK_MEDIA_NEXT = 0xB0
VK_MEDIA_PREV = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x02


@dataclass
class MediaState:
    title: str = ""
    artist: str = ""
    album: str = ""
    status: str = "none"  # playing / paused / stopped / none
    has_session: bool = False
    position: float = 0.0          # 秒，SMTC 采样那一刻的进度
    duration: float = 0.0          # 秒
    stamp: float = 0.0             # 采样时的 time.monotonic()
    cover: bytes = b""             # 封面 PNG/JPEG 原始字节
    cover_key: str = ""            # 封面归属的曲目标识
    has_position: bool = True      # 有没有真实播放进度（标题兜底时没有）
    playing_known: bool = True     # 状态是"测出来的"还是"沿用上一次/用户点击的结果"

    @property
    def label(self) -> str:
        if not self.has_session:
            return "暂无音乐"
        parts = [p for p in (self.title, self.artist) if p]
        return " - ".join(parts) if parts else "正在播放"

    @property
    def track_key(self) -> str:
        return f"{self.title}|{self.artist}|{self.album}"

    def live_position(self) -> float:
        """播放中按流逝时间推算当前进度，暂停/停止就停在采样点。"""
        if self.status != "playing" or not self.stamp:
            return self.position
        import time as _t
        pos = self.position + (_t.monotonic() - self.stamp)
        if self.duration:
            pos = min(pos, self.duration)
        return max(0.0, pos)


def _seconds(ts) -> float:
    """winsdk 的 TimeSpan 是 timedelta，兜底也接受数字。"""
    if ts is None:
        return 0.0
    try:
        return float(ts.total_seconds())
    except AttributeError:
        try:
            return float(ts) / 10_000_000  # 100ns tick
        except Exception:
            return 0.0


def _map_status(raw) -> str:
    # GlobalSystemMediaTransportControlsSessionPlaybackStatus
    playing = 3
    paused = 4
    stopped = 5
    value = getattr(raw, "value", raw)
    if value == playing:
        return "playing"
    if value == paused:
        return "paused"
    if value == stopped:
        return "stopped"
    return "stopped"


def _monotonic() -> float:
    import time
    return time.monotonic()


def _media_key(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def _pid_of(hwnd: int) -> int:
    """窗口所属进程 id（拿不到返回 0）。"""
    try:
        import win32process
        return win32process.GetWindowThreadProcessId(hwnd)[1]
    except Exception:
        return 0


class MediaController:
    def __init__(self) -> None:
        self._state = MediaState()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._commands: Optional[asyncio.Queue] = None
        self._stop = threading.Event()
        self._stopped = False
        self._winsdk = False
        self._cover_cache: dict = {}
        self._fallback = None
        self._probe = None
        self._progress = None
        self._bar = None
        self._motion = None
        self._intent: Optional[tuple] = None      # (用户刚点出的状态, 时刻)
        self._last_status = "playing"             # 读不到证据时沿用的上一次判断

    def mark_intent(self, playing: bool) -> None:
        """用户点了播放/暂停键：先按点击结果走，等真实证据出来再覆盖。

        静音时峰值恒为 0、进度线也读不到，光靠探测可能几秒都拿不出结论，
        这段时间界面必须沿着"用户刚按下"的方向走，不能翻回去。
        """
        import time as _t
        self._intent = (bool(playing), _t.monotonic())

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """退出时停掉后台循环。

        这个收尾会被调用不止一次（托盘退出 → aboutToQuit → app.exec 返回），
        而且后台线程可能已经自己把循环关掉了。重复调用、循环已关闭都要当正常情况，
        否则 call_soon_threadsafe 会抛 RuntimeError("Event loop is closed")，
        无控制台的 exe 就会弹“Unhandled exception in script”对话框。
        """
        if self._stopped:
            return
        self._stopped = True
        self._stop.set()
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            # 线程恰好在这一刻关掉循环，正常收尾
            pass

    def get_state(self) -> MediaState:
        """SMTC 有会话就用它；没有就用播放器窗口标题 + 网易云接口兜底。"""
        state = self._state
        if state.has_session:
            # 系统会话也有 1 秒左右的更新延迟，刚点完的 1.2 秒内同样以点击结果为准
            from dataclasses import replace
            return replace(state, status=self._decide(state.status, True))
        try:
            if self._fallback is None:
                from core.nowplaying import NowPlayingFallback
                self._fallback = NowPlayingFallback()
            if self._probe is None:
                from core.audiostate import PlaybackProbe
                self._probe = PlaybackProbe()
            info = self._fallback.read()
            if not info:
                return state
            spec, title, artist, hwnd = info
            cover = self._fallback.cover(title, artist)
            status = self._last_status
            pid = _pid_of(hwnd)
            if self._progress is None:
                from core.progress import ProgressEstimator
                from core.progressbar import BAR_SPECS, BarReader, MotionWatch
                self._progress = ProgressEstimator()
                spec_bar = BAR_SPECS.get(spec.key)
                self._bar = BarReader(spec_bar) if spec_bar else None
                self._motion = MotionWatch(self._bar) if self._bar else None
            known = False
            if pid:
                # 标题只能说明"放的是哪首"，播放/暂停要看这个进程到底在不在走
                playing = self._probe.is_playing(pid)
                if playing is None and self._motion is not None:
                    # 静音/状态读不准时，改看播放器窗口里的进度线动不动
                    playing = self._motion.judge(hwnd, pid)
                if playing is not None:
                    known = True
                    status = "playing" if playing else "paused"
            status = self._decide(status, known)
            # 没有系统时间轴：窗口露出进度线就读真实进度，读不到再按时间推算
            key = f"{title}|{artist}"
            position = self._progress.update(key, status == "playing")
            duration = 0.0
            if self._bar is not None and pid:
                from core import lyrics as lyrics_mod
                ratio = self._bar.read(hwnd, pid)
                duration = lyrics_mod.cached_duration(title, artist)
                if ratio is not None and duration > 0:
                    # 暂停时读到的也是"停在哪"，恢复播放就能接着算
                    position = self._progress.seek(ratio * duration, duration)
            return MediaState(
                title=title, artist=artist, album="", status=status,
                has_session=True, cover=cover, cover_key=f"{title}|{artist}",
                position=position, duration=duration, stamp=0.0, has_position=True,
                playing_known=known,
            )
        except Exception:
            return state

    def _decide(self, measured: str, known: bool) -> str:
        """把"测出来的状态""用户刚点的意图""上次的判断"合成一个结果。

        - 刚点完 1.2 秒内，探测可能还没反应过来（播放器要收键、进度线要更新），
          一律按用户点的方向走，避免图标先翻回去再翻过来；
        - 1.2 秒后证据说了算；证据一直拿不到就继续按用户的点击走，最多 10 秒，
          之后沿用上一次的结论。
        """
        import time as _t
        now = _t.monotonic()
        intent = self._intent
        if intent is not None:
            want, at = intent
            age = now - at
            if known and age >= 1.2:
                self._intent = None
                self._last_status = measured
                return measured
            if known:
                # 刚点完，证据还没跟上，先按点击结果
                self._last_status = "playing" if want else "paused"
                return self._last_status
            if age < 10.0:
                self._last_status = "playing" if want else "paused"
                return self._last_status
            self._intent = None
            return self._last_status
        if known:
            self._last_status = measured
        return self._last_status

    # ---- 控制指令（线程安全投递）----
    def toggle_play_pause(self) -> None:
        self._dispatch("toggle")

    def next_track(self) -> None:
        self._dispatch("next")

    def prev_track(self) -> None:
        self._dispatch("prev")

    def _dispatch(self, cmd: str) -> None:
        loop = self._loop
        if loop is not None and self._commands is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(lambda: self._commands.put_nowait(cmd))
                return
            except RuntimeError:
                pass       # 循环刚被关掉，退回媒体键
        # 无 winsdk 后台循环：直接媒体键兜底
        self._fallback_key(cmd)

    @staticmethod
    def _fallback_key(cmd: str) -> None:
        if cmd == "next":
            _media_key(VK_MEDIA_NEXT)
        elif cmd == "prev":
            _media_key(VK_MEDIA_PREV)
        else:
            _media_key(VK_MEDIA_PLAY_PAUSE)

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except RuntimeError:
            # 退出竞态下循环被关掉，属于正常收尾
            pass
        finally:
            self._forget_loop()

    def _forget_loop(self) -> None:
        """循环退出后清掉引用，外部再调 stop()/投指令就不会摸到已关闭的循环。"""
        self._loop = None
        self._commands = None

    async def _main(self) -> None:
        try:
            await self._loop_body()
        finally:
            # 早退分支（没有 winsdk）也不能把半截引用留给外面
            self._forget_loop()

    async def _loop_body(self) -> None:
        try:
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as Manager,
            )
            self._winsdk = True
        except ImportError:
            self._winsdk = False
            while not self._stop.is_set():
                await asyncio.sleep(2)
            return

        self._loop = asyncio.get_running_loop()
        self._commands = asyncio.Queue()
        mgr = await Manager.request_async()

        while not self._stop.is_set():
            await self._poll(mgr)
            # 等待控制指令或超时；1 秒一轮，切歌/暂停都跟得上
            try:
                cmd = await asyncio.wait_for(self._commands.get(), timeout=1.0)
                await self._handle_command(mgr, cmd)
                await self._poll(mgr)      # 命令执行完立刻回读，别等下一轮
            except asyncio.TimeoutError:
                pass

    async def _poll(self, mgr) -> None:
        try:
            session = mgr.get_current_session()
            if session is None:
                self._state = MediaState(status="none", has_session=False)
                return
            props = await session.try_get_media_properties_async()
            info = session.get_playback_info()
            timeline = session.get_timeline_properties()
            key = f"{props.title}|{props.artist}|{props.album_title}"
            if key not in self._cover_cache:
                if len(self._cover_cache) > 40:
                    self._cover_cache.clear()
                self._cover_cache[key] = await self._read_thumbnail(props)
            cover = self._cover_cache.get(key, b"")
            self._state = MediaState(
                title=props.title or "",
                artist=props.artist or "",
                album=props.album_title or "",
                status=_map_status(info.playback_status),
                has_session=True,
                position=_seconds(timeline.position),
                duration=_seconds(timeline.end_time),
                stamp=_monotonic(),
                cover=cover or b"",
                cover_key=key,
            )
        except Exception:
            # 单次读取失败保留上一状态，不让后台线程退出
            pass

    @staticmethod
    async def _read_thumbnail(props) -> bytes:
        """取封面字节。SMTC 给的是随机访问流，这里读到内存里交给界面解码。"""
        try:
            ref = props.thumbnail
            if ref is None:
                return b""
            stream = await ref.open_read_async()
            size = int(stream.size or 0)
            if size <= 0 or size > 8 * 1024 * 1024:
                return b""
            from winsdk.windows.storage.streams import DataReader
            reader = DataReader(stream)
            await reader.load_async(size)
            buf = bytearray(size)
            reader.read_bytes(buf)
            return bytes(buf)
        except Exception:
            return b""

    async def _handle_command(self, mgr, cmd: str) -> None:
        try:
            session = mgr.get_current_session()
            if session is None:
                self._fallback_key(cmd)
                return
            if cmd == "next":
                await session.try_next_async()
            elif cmd == "prev":
                await session.try_previous_async()
            else:
                info = session.get_playback_info()
                if _map_status(info.playback_status) == "playing":
                    await session.try_pause_async()
                else:
                    await session.try_play_async()
        except Exception:
            self._fallback_key(cmd)
