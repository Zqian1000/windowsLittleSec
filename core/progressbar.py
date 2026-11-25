"""从播放器窗口底部那根进度线读真实播放进度（比例 0~1）。

为什么要读像素：网易云桌面版不注册系统媒体时间轴，窗口标题只有歌名，
所以过去只能靠"切歌归零 + 流逝时间"猜进度。但它的播放条上一直画着一根
横贯窗口的进度线（已播放段亮、未播放段暗），只要窗口没有被别的窗口盖住，
扫一行像素就能得到准确比例，再乘以曲长就是真实进度。

窗口最小化/被遮挡时读不到，返回 None，调用方退回按时间推算。
只读屏幕上这根线的颜色，不截取内容、不上传。
"""
from __future__ import annotations

import ctypes
import threading
import time
from dataclasses import dataclass
from typing import Optional

u = ctypes.windll.user32
g = ctypes.windll.gdi32

u.GetDC.restype = ctypes.c_void_p
u.GetDC.argtypes = [ctypes.c_void_p]
g.CreateCompatibleDC.restype = ctypes.c_void_p
g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
g.CreateCompatibleBitmap.restype = ctypes.c_void_p
g.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
g.SelectObject.restype = ctypes.c_void_p
g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
g.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                     ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                     ctypes.c_uint]


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


g.GetDIBits.restype = ctypes.c_int
g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


@dataclass(frozen=True)
class BarSpec:
    """某个播放器的进度线位置约定。"""

    key: str
    # 扫描带：距窗口底部的距离占窗口高度的比例（lo 更靠近底部）
    band_lo: float = 0.06
    band_hi: float = 0.22
    min_width: int = 500          # 窗口太窄时线太短，比例不靠谱
    step_min: int = 22            # 已播放/未播放两段的亮度差下限


BAR_SPECS = {
    "cloudmusic": BarSpec("cloudmusic"),
}


def _grab(rect) -> Optional[tuple]:
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    if w <= 0 or h <= 0:
        return None
    hdc = u.GetDC(None)
    try:
        mdc = g.CreateCompatibleDC(hdc)
        bmp = g.CreateCompatibleBitmap(hdc, w, h)
        g.SelectObject(mdc, bmp)
        if not g.BitBlt(mdc, 0, 0, w, h, hdc, rect[0], rect[1], 0x00CC0020):
            return None
        buf = ctypes.create_string_buffer(w * h * 4)
        bi = _BIH()
        bi.biSize = ctypes.sizeof(_BIH)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        if not g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0):
            return None
        return w, h, buf.raw
    finally:
        u.ReleaseDC(None, hdc)


def _brightness(data: bytes, w: int, x: int, y: int) -> int:
    i = (y * w + x) * 4
    return (data[i + 2] + data[i + 1] + data[i]) // 3


def _line_ratio(data: bytes, w: int, y: int, step: int) -> Optional[tuple]:
    """在一行里找"一次亮度跳变"的位置，返回 (比例, 跳变幅度)。

    网易云的进度线是"已播放：从暗到亮渐变 / 未播放：一段平色"，
    所以只要求跳变唯一、跳变右侧平坦，不能要求整行平坦。
    """
    xs = list(range(step, max(step + 1, w - step), step))
    vals = [_brightness(data, w, x, y) for x in xs]
    diffs = [abs(vals[i + 1] - vals[i]) for i in range(len(vals) - 1)]
    if len(diffs) < 6:
        return None
    biggest = max(diffs)
    if biggest < 12:
        return None
    idx = diffs.index(biggest)
    others = [d for i, d in enumerate(diffs) if abs(i - idx) > 2]
    if others and max(others) > max(12, biggest * 0.5):
        return None
    if sum(others) / max(1, len(others)) > 6:
        return None
    left = sum(vals[:idx + 1]) / max(1, idx + 1)
    right = sum(vals[idx + 1:]) / max(1, len(vals) - idx - 1)
    if left <= right:            # 已播放段应该更亮
        return None
    return (xs[idx] + step / 2) / max(1, w), biggest


class BarReader:
    """读窗口底部进度线；带 0.6 秒缓存，界面每次刷新都调也不贵。"""

    def __init__(self, spec: BarSpec, ttl: float = 0.6) -> None:
        self.spec = spec
        self._ttl = ttl
        self._lock = threading.Lock()
        self._cache: Optional[tuple] = None      # (hwnd, ratio, at)

    def read(self, hwnd: int, pid: int) -> Optional[float]:
        if not hwnd or not pid:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache
            if cached and cached[0] == hwnd and now - cached[2] < self._ttl:
                return cached[1]
        ratio = None
        try:
            ratio = self._sample(hwnd, pid)
        except Exception:
            ratio = None
        with self._lock:
            self._cache = (hwnd, ratio, now)
        return ratio

    def _sample(self, hwnd: int, pid: int) -> Optional[float]:
        import win32gui
        import win32process

        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return None
        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w < self.spec.min_width or h < 300:
            return None
        y_lo = rect[3] - int(h * self.spec.band_hi)
        y_hi = rect[3] - int(h * self.spec.band_lo)
        # 采样带中间那一点必须仍属于这个播放器窗口，否则说明被别的窗口压住了
        probe_x, probe_y = rect[0] + w // 2, (y_lo + y_hi) // 2
        hit = win32gui.WindowFromPoint((probe_x, probe_y))
        if not hit or win32process.GetWindowThreadProcessId(hit)[1] != pid:
            return None

        grab = _grab((rect[0], y_lo, rect[2], y_hi))
        if grab is None:
            return None
        gw, gh, data = grab
        best = None
        coarse = max(6, gw // 200)          # 先粗扫找行，再对那一行细扫
        for row in range(gh):
            got = _line_ratio(data, gw, row, coarse)
            if not got:
                continue
            ratio, jump = got
            if jump < self.spec.step_min:
                continue
            # 跳变点不能贴边（贴边通常是被裁掉的图片/分隔线）
            if ratio < 0.02 or ratio > 0.985:
                continue
            score = jump - abs(ratio - 0.5) * 4
            if best is None or score > best[0]:
                best = (score, ratio, row)
        if best is None:
            return None
        refined = _line_ratio(data, gw, best[2], 2)
        return (refined[0] if refined else best[1])


class MotionWatch:
    """看进度线有没有在动：窗口露出来时，这比峰值、会话状态都更能说明"在放还是暂停"。

    静音时峰值恒 0、会话状态可能一直是 Active，只有进度线不会骗人：
    在走就是播放，长时间不动就是暂停（拖进度条的那一下除外）。
    """

    def __init__(self, reader: BarReader, still_for: float = 1.6,
                 min_delta: float = 0.0015) -> None:
        self.reader = reader
        self.still_for = still_for
        self.min_delta = min_delta
        self._last_ratio = None
        self._last_change = 0.0

    def judge(self, hwnd: int, pid: int) -> Optional[bool]:
        ratio = self.reader.read(hwnd, pid)
        now = time.monotonic()
        if ratio is None:
            self._last_ratio = None
            return None
        last = self._last_ratio
        self._last_ratio = ratio
        if last is None:
            self._last_change = now
            return None
        if abs(ratio - last) >= self.min_delta:
            self._last_change = now
            return True
        if now - self._last_change >= self.still_for:
            return False
        return None
