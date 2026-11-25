"""未读检测：任务栏按钮 + 托盘图标的红点／闪动。

两条信号：
1) 闪动：窗口请求注意时任务栏按钮或托盘图标会明暗交替，短时间内像素波动大；
2) 红点：未读徽标是红色小块，统计区域内的红色像素数量即可判断（静止图标基线为 0）。

只读屏幕像素，不读聊天内容、不联网。多显示器会同时采两块屏上的按钮。
"""
from __future__ import annotations

import ctypes
import time
from collections import deque
from typing import Dict, List, Optional, Tuple, Deque, Set

import psutil
import uiautomation as auto
import win32gui
import win32process

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

user32.GetDC.restype = ctypes.c_void_p
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.BitBlt.restype = ctypes.c_int
gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                         ctypes.c_int, ctypes.c_int, ctypes.c_uint]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                            ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_uint]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


Rect = Tuple[int, int, int, int]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _cursor_pos() -> Tuple[int, int]:
    p = _POINT()
    try:
        user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y
    except Exception:
        return -10000, -10000


def _cursor_near(rect: Rect, pad: int = 6) -> bool:
    """鼠标是否停在这块图标上/边上。

    鼠标悬停会让任务栏按钮和托盘图标整体变亮、还带 tooltip 阴影，绿通道
    波动足以骗过“闪动”判定。悬停期间本来也看不清闪烁，直接不采样。
    """
    x, y, w, h = rect
    cx, cy = _cursor_pos()
    return (x - pad) <= cx <= (x + w + pad) and (y - pad) <= cy <= (y + h + pad)


def _grab_stats(rect: Rect) -> Optional[Tuple[float, int]]:
    """抓一块区域，返回（绿通道均值, 红色像素数）。失败返回 None。"""
    x, y, w, h = rect
    if w <= 0 or h <= 0:
        return None
    hdc_screen = user32.GetDC(None)
    if not hdc_screen:
        return None
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    buf = ctypes.create_string_buffer(w * h * 4)
    try:
        old = gdi32.SelectObject(hdc_mem, bmp)
        if not gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, 0x00CC0020):
            return None
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0
        if not gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf, ctypes.byref(bi), 0):
            return None
        data = buf.raw
        total = 0
        red = 0
        n = w * h
        for i in range(0, len(data) - 3, 4):
            b = data[i]
            g = data[i + 1]
            r = data[i + 2]
            total += g
            if r > 140 and g < 90 and b < 90:
                red += 1
        return (total / n, red) if n else None
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


# 名称前缀（企业微信要排在微信前，避免被误吞）
_PREFIXES = [
    ("wecom", ("企业微信",)),
    ("wechat", ("微信",)),
    ("feishu", ("飞书",)),
    ("dingtalk", ("钉钉",)),
    ("qq", ("QQ", "腾讯QQ")),
]

# 进程名 → 客户端键（托盘态只能靠进程自己弹出的通知窗判断）
_EXE_KEYS = {
    "weixin.exe": "wechat",
    "wechat.exe": "wechat",
    "wxwork.exe": "wecom",
    "feishu.exe": "feishu",
    "dingtalk.exe": "dingtalk",
    "qq.exe": "qq",
    "tim.exe": "qq",
}

# 通知窗尺寸范围（主窗口远大于此，辅助窗口远小于此）
POPUP_MIN_W, POPUP_MAX_W = 120, 780
POPUP_MIN_H, POPUP_MAX_H = 40, 420

# 标题非空也不该算“新消息”的辅助窗类名片段（更新、告警、登录、文档公告等）
_POPUP_CLASS_BLOCK = (
    "hidsdk", "warning", "upgrade", "update", "login", "crashpad",
    "shadow", "titlebar", "tooltips", "ime", "systemmessage",
    "powermessage", "displayicc", "docnotice", "webview",
)

# 主窗口标题（与客户端同名，用来把被缩小的主窗口排除出「通知窗」判定）
_MAIN_TITLES = {
    "wechat": ("微信",),
    "wecom": ("企业微信",),
    "feishu": ("飞书",),
    "dingtalk": ("钉钉",),
    "qq": ("QQ", "腾讯QQ"),
}


def _match_key(name: str) -> Optional[str]:
    for key, prefixes in _PREFIXES:
        if any(name.startswith(p) for p in prefixes):
            return key
    return None


class FlashWatcher:
    """按客户端采样任务栏按钮与托盘图标，识别闪动或红点 → 未读。"""

    SAMPLE_WINDOW = 14       # 每个采样点保留的样本数
    SPAN_THRESHOLD = 16.0    # 绿通道极差阈值（实测静止 0、闪动 30+）
    BADGE_PIXELS = 25        # 红点像素数阈值（静止基线实测 0）
    LOOKUP_INTERVAL = 20.0   # 位置缓存刷新间隔（秒，UIA 只遍历任务栏子树）
    PID_CACHE = 10.0         # 进程名映射缓存（秒）
    POPUP_HOLD = 6.0         # 通知窗消失后红点再保留多久（秒）
    FLASH_HOLD = 4.0         # 检出闪动后红点保留多久（秒，不再永久亮着）
    RED_CONFIRM = 2          # 红点要连续几拍都采到才算数（托盘图标错位会偶尔扫到别人的红点）
    RESET_GRACE = 1.5        # 刚判定前台/刚清过，这期间新冒出来的小窗不算新消息
    POPUP_CONFIRM = 0.6      # 小窗要连着存在这么久才认成通知（过滤加载窗/过渡窗）

    def __init__(self) -> None:
        self._taskbar: Dict[str, List[Rect]] = {}
        self._tray: Dict[str, List[Rect]] = {}
        self._series: Dict[Tuple[str, int, int], Deque[float]] = {}
        self._flash_until: Dict[str, float] = {}
        self._badge: Dict[str, bool] = {}
        self._red_streak: Dict[str, int] = {}
        self._last_lookup = 0.0
        self._uia_ok = True
        self._pids: Dict[int, str] = {}
        self._pid_time = 0.0
        self._popup_until: Dict[str, float] = {}
        self._popup_seen: Dict[int, float] = {}
        self._popup_first: Dict[int, float] = {}
        self._reset_ts: Dict[str, float] = {}
        self._primed = False

    # ---------- 位置定位 ----------
    def _taskbar_windows(self) -> List[int]:
        """收集所有显示器上的任务栏窗口句柄（主屏 + 副屏）。"""
        found: List[int] = []

        def enum(hwnd, _):
            try:
                cls = win32gui.GetClassName(hwnd)
            except Exception:
                return True
            if cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                found.append(hwnd)
            return True

        try:
            win32gui.EnumWindows(enum, None)
        except Exception:
            return found
        return found

    def _locate(self) -> None:
        """只遍历任务栏子树，记录任务栏按钮与托盘图标区域（含所有显示器）。"""
        if not self._uia_ok:
            return
        taskbar: Dict[str, List[Rect]] = {}
        tray: Dict[str, List[Rect]] = {}

        def walk(ctrl, depth: int = 0) -> None:
            if depth > 12:
                return
            try:
                cls = ctrl.ClassName or ""
                name = ctrl.Name or ""
            except Exception:
                return
            if name:
                key = _match_key(name)
                if key and "TaskListButton" in cls and "运行窗口" in name:
                    r = ctrl.BoundingRectangle
                    w, h = int(r.width()), int(r.height())
                    if w >= 20 and h >= 20:
                        taskbar.setdefault(key, []).append(
                            (int(r.left), int(r.top), w, h))
                elif key and ("SystemTray" in cls or "ToolbarWindow32" in cls):
                    r = ctrl.BoundingRectangle
                    w, h = int(r.width()), int(r.height())
                    if w >= 16 and h >= 16:
                        tray.setdefault(key, []).append(
                            (int(r.left), int(r.top), w, h))
            try:
                for c in ctrl.GetChildren():
                    walk(c, depth + 1)
            except Exception:
                return

        try:
            hwnds = self._taskbar_windows()
            if not hwnds:
                walk(auto.GetRootControl())
            for hwnd in hwnds:
                try:
                    walk(auto.ControlFromHandle(hwnd))
                except Exception:
                    continue
        except Exception:
            self._uia_ok = False
            return

        self._taskbar = taskbar
        self._tray = tray
        self._last_lookup = time.monotonic()

    # ---------- 采样与判定 ----------
    def _pid_map(self) -> Dict[int, str]:
        """进程 id → 客户端键，缓存一段时间避免反复枚举进程。"""
        now = time.monotonic()
        if now - self._pid_time < self.PID_CACHE and self._pids:
            return self._pids
        found: Dict[int, str] = {}
        try:
            for p in psutil.process_iter(["name", "pid"]):
                key = _EXE_KEYS.get((p.info["name"] or "").lower())
                if key:
                    found[p.info["pid"]] = key
        except Exception:
            return self._pids
        self._pids = found
        self._pid_time = now
        return found

    def _scan_popups(self) -> Set[str]:
        """找出各客户端自己弹出的新消息通知窗（托盘态新消息的唯一外部表现）。

        关键约束：只认“这次新出现”的窗口。客户端常驻的辅助窗（飞书 HID 通知窗、
        企微标题栏/文档公告窗）尺寸也落在小窗区间，如果只看“存在且可见”，
        就会在没有新消息时一直亮红点。
        """
        pids = self._pid_map()
        if not pids:
            return set()
        now = time.monotonic()
        hits: Set[str] = set()
        areas: Dict[str, List[Tuple[int, int]]] = {}
        seen: Dict[int, float] = {}

        def cb(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                key = pids.get(pid)
                if not key:
                    return True
                cls = win32gui.GetClassName(hwnd) or ""
                cls_l = cls.lower()
                if cls.startswith(("IME", "MSCTFIME", "GDI+", "tooltips_class32",
                                   "PseudoConsoleWindow", "crashpad",
                                   "Chrome_SystemMessageWindow", "Base_PowerMessage",
                                   "DisplayICC")):
                    return True
                if any(b in cls_l for b in _POPUP_CLASS_BLOCK):
                    return True
                title = (win32gui.GetWindowText(hwnd) or "").strip()
                if title in _MAIN_TITLES.get(key, ()):
                    return True
                # 无标题的窗口只接受真实 toast（不再放行任何名字带 notif 的辅助窗）
                if not title and "toast" not in cls_l:
                    return True
                r = win32gui.GetWindowRect(hwnd)
                w, h = r[2] - r[0], r[3] - r[1]
                if w <= 0 or h <= 0:
                    return True
                areas.setdefault(key, []).append((w * h, hwnd))
                if POPUP_MIN_W <= w <= POPUP_MAX_W and POPUP_MIN_H <= h <= POPUP_MAX_H:
                    seen[hwnd] = now
                    first = self._popup_first.setdefault(hwnd, now)
                    # 本轮之前没见过、且连着存在了 0.6 秒 → 是刚弹出来的通知。
                    # 刚清过未读（点了图标/切到前台/读完切走）的头一秒半，
                    # 客户端常会顺手建/挪几个小窗（加载窗、聊天浮窗），别又算成新消息。
                    fresh = now - self._reset_ts.get(key, -999.0) >= self.RESET_GRACE
                    if (self._primed and fresh and hwnd not in self._popup_seen
                            and now - first >= self.POPUP_CONFIRM - 1e-6):
                        hits.add(key)
                        self._popup_seen[hwnd] = now
            except Exception:
                return True

        try:
            win32gui.EnumWindows(cb, None)
        except Exception:
            pass

        # 主窗口恰好落在尺寸区间内时，按面积把它排除掉
        for key, items in areas.items():
            if len(items) > 1:
                main_hwnd = max(items)[1]
                r = win32gui.GetWindowRect(main_hwnd)
                w, h = r[2] - r[0], r[3] - r[1]
                if POPUP_MIN_W <= w <= POPUP_MAX_W and POPUP_MIN_H <= h <= POPUP_MAX_H:
                    hits.discard(key)

        # 首轮把已经在的小窗登记成“老窗口”，之后只对真正新出现且活过
        # 0.6 秒的窗口报新消息；登记过的窗口刷新时间戳，10 秒没出现就清掉。
        if not self._primed:
            for hwnd, ts in seen.items():
                self._popup_seen[hwnd] = ts
        else:
            for hwnd, ts in seen.items():
                if hwnd in self._popup_seen:
                    self._popup_seen[hwnd] = ts
        for hwnd in list(self._popup_first):
            if hwnd not in seen:
                self._popup_first.pop(hwnd, None)
        for hwnd, ts in list(self._popup_seen.items()):
            if now - ts > 10.0:
                self._popup_seen.pop(hwnd, None)
        self._primed = True
        return hits

    def _looks_like_flash(self, series: Deque[float]) -> bool:
        """真闪动是明暗交替：至少 3 次跨中线来回。单纯变亮/变暗不算。"""
        if len(series) < 6:
            return False
        lo, hi = min(series), max(series)
        if hi - lo < self.SPAN_THRESHOLD:
            return False
        mid = (lo + hi) / 2.0
        flips = 0
        for a, b in zip(series, list(series)[1:]):
            if (a < mid) != (b < mid):
                flips += 1
        return flips >= 3

    def poll(self, enabled: Dict[str, bool],
             foreground: Dict[str, bool],
             active: Optional[Dict[str, bool]] = None) -> Dict[str, bool]:
        """采样一次并返回各客户端是否未读。

        active：该客户端当前是否有正常（未收进托盘）的窗口。托盘态下任务栏
        按钮不存在，缓存的旧矩形不可信，只保留通知窗信号。
        """
        now = time.monotonic()
        if now - self._last_lookup > self.LOOKUP_INTERVAL:
            self._locate()

        popups = self._scan_popups()
        for key in popups:
            self._popup_until[key] = now + self.POPUP_HOLD

        state: Dict[str, bool] = {}
        for key, on in enabled.items():
            if not on or foreground.get(key):
                self._reset(key)
                state[key] = False
                continue

            popup = now < self._popup_until.get(key, 0.0)
            is_active = True if active is None else bool(active.get(key, False))
            rects = (self._taskbar.get(key, []) + self._tray.get(key, [])
                     if is_active else [])
            if not rects:
                # 收进托盘/没有任务栏按钮时根本采不到这块像素：
                # 旧的红点必须扔掉，不然用户早就读完了，图标还一直亮着。
                self._badge[key] = False
                self._red_streak[key] = 0
                state[key] = bool(now < self._flash_until.get(key, 0.0) or popup)
                continue

            badge = False
            for idx, rect in enumerate(rects):
                # 鼠标悬停在图标上时不采闪动样本（悬停高亮会被误判成闪动），
                # 红点仍然照常看，红点是静态的，不受高亮影响。
                hovering = _cursor_near(rect)
                res = _grab_stats(rect)
                if res is None:
                    continue
                mean_g, red = res
                if red >= self.BADGE_PIXELS:
                    badge = True
                skey = (key, idx, rect[1])
                if hovering:
                    self._series.pop(skey, None)
                    continue
                series = self._series.setdefault(
                    skey, deque(maxlen=self.SAMPLE_WINDOW))
                series.append(mean_g)
                if self._looks_like_flash(series):
                    self._flash_until[key] = now + self.FLASH_HOLD

            # 红点要连拍确认：托盘图标错位时偶尔会扫到旁边图标的红点，
            # 一拍就算会把"没有新消息"误报成有。
            if badge:
                self._red_streak[key] = self._red_streak.get(key, 0) + 1
            else:
                self._red_streak[key] = 0
            confirmed = self._red_streak.get(key, 0) >= self.RED_CONFIRM
            self._badge[key] = confirmed
            state[key] = bool(now < self._flash_until.get(key, 0.0)
                              or confirmed or popup)

        return state

    def _reset(self, key: str) -> None:
        self._flash_until[key] = 0.0
        self._badge[key] = False
        self._red_streak[key] = 0
        self._popup_until[key] = 0.0
        self._reset_ts[key] = time.monotonic()
        for k in [k for k in self._series if k[0] == key]:
            self._series.pop(k, None)

    def clear(self, key: str) -> None:
        """用户点击图标/切到该窗口后清掉红点。"""
        self._reset(key)
