"""IM 客户端识别、账号枚举与窗口唤起（面向对象版）。

结构：
    IMClient（基类）            —— 一个客户端的全部差异都写在子类里
      ├─ WeChatClient  微信
      ├─ WeComClient   企业微信
      ├─ FeishuClient  飞书
      ├─ QQClient      QQ
      └─ DingTalkClient 钉钉
    IMClientRoster             —— 注册表，按进程名/类名找客户端
    IMAccount                  —— 枚举出来的一条账号（一个主窗口）
    Activator → ShellActivator / ForceActivator
                               —— 唤起方式，由 IMClient.activation_order 决定顺序

加一个客户端（例如新出的钉钉/其他）只需要写一个 IMClient 子类并 register()，
不用改枚举、去重、唤起、界面任何一处。

仅读取本机窗口标题/类名/进程名与任务栏控件的可访问性信息，不读聊天内容、不联网。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    import win32api
    import win32gui
    import win32process
    import win32con
    import psutil
    _HAS_WIN32 = True
except ImportError:  # 依赖未就绪时降级为空列表，保证界面不崩
    _HAS_WIN32 = False

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
gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                         ctypes.c_uint]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                            ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_uint]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]


class WINPOINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class WINRECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class WINPLACEMENT(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint), ("flags", ctypes.c_uint),
                ("showCmd", ctypes.c_uint), ("ptMinPosition", WINPOINT),
                ("ptMaxPosition", WINPOINT), ("rcNormalPosition", WINRECT)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


# 不视为主窗口的系统/浮层类名
_CLASS_BLOCKLIST = {
    "traynotifywnd", "tooltipclass", "workerw", "shell_traywnd",
    "notifysicon", "overflowwindowforoverflowxamlisland",
    "windows.ui.core.corewindow", "default ime",
}


# ---------------------------------------------------------------------------
# 客户端模型
# ---------------------------------------------------------------------------
class IMClient:
    """一个 IM 客户端的全部差异都收敛在这个对象里。

    子类只需要声明：进程名、主窗口类名规则、托盘/任务栏上的可访问性名字、
    以及唤起顺序。枚举、去重、界面渲染都读这些声明，不需要针对单个客户端写分支。
    """

    key: str = ""
    name: str = ""
    exes: Tuple[str, ...] = ()
    class_exact: Tuple[str, ...] = ()
    class_suffix: Tuple[str, ...] = ()
    class_prefix: Tuple[str, ...] = ()
    # 任务栏按钮 / 托盘图标在系统 UI 里的名字前缀
    shell_names: Tuple[str, ...] = ()
    # 唤起顺序：
    #   launch = 像双击快捷方式那样再启动一次客户端，单实例客户端会把请求交给
    #            已运行的实例，由它自己显示主窗口（不碰托盘浮层，不闪屏）；
    #   shell  = 点任务栏按钮 / 托盘图标（同样让客户端自己显示，但图标在
    #            「隐藏图标」浮层里时会先弹出右下角面板）；
    #   force  = 外部 ShowWindow + 还原位置 + 前台（最后兜底）。
    activation_order: Tuple[str, ...] = ("launch", "shell", "force")
    activation_timeout: float = 2.5
    # 再启动一次能不能唤起原实例。微信 4.x 会真的多开一个实例（弹一下再自己
    # 退出），所以微信必须关掉这条，退回到托盘入口。
    supports_launch: bool = True
    # 同一进程下最多算几个账号（主窗口 + 辅助窗口会并存）
    accounts_per_process: int = 1
    # 收进托盘后主窗口是否还留在 (0,0)（钉钉实测如此），这类窗口不能当空壳丢掉
    tray_keeps_zero_rect: bool = False

    def matches(self, exe: Optional[str], cls: str) -> bool:
        if not exe or exe.lower() not in self.exes:
            return False
        if not (self.class_exact or self.class_suffix or self.class_prefix):
            return True                      # 兜底客户端：该进程有标题的窗口都算
        if cls in self.class_exact:
            return True
        return (any(cls.endswith(s) for s in self.class_suffix)
                and any(cls.startswith(p) for p in self.class_prefix))

    def is_main_window_class(self, cls: str) -> bool:
        """隐藏窗口必须靠类名判定，避免把登录/更新/通知等辅助窗当成账号。"""
        if not (self.class_exact or self.class_suffix or self.class_prefix):
            return True
        return (cls in self.class_exact
                or (any(cls.endswith(s) for s in self.class_suffix)
                    and any(cls.startswith(p) for p in self.class_prefix)))

    def activate(self, hwnd: int) -> bool:
        """按声明的顺序唤起窗口，成功即停。

        判定标准是"窗窗口真的可看可用"（_is_ready），不是"手段调用成功"：
        点任务栏按钮/托盘图标这类操作对 UIA 永远返回成功，窗口却可能没出来，
        所以每一种手段之后都要轮询验证，没到位就换下一种，不能一条道走到黑。
        """
        if not _HAS_WIN32 or not win32gui.IsWindow(hwnd):
            return False
        # 已经在用且在前台：直接返回，避免再点一次任务栏按钮把它最小化
        if _is_ready(hwnd) and _is_foreground(hwnd):
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        for kind in self.activation_order:
            activator = ACTIVATORS.get(kind)
            if activator is None:
                continue
            try:
                triggered = activator.trigger(self, hwnd)
            except Exception:
                triggered = False
            if not triggered:
                continue
            target = _wait_ready_target(self, hwnd, pid, self.activation_timeout)
            if target:
                # 任务栏/托盘的点击把窗口叫起来了，但前台可能还停在系统浮层
                # （比如“显示隐藏的图标”那个小窗），这里再把前台明确交给客户端。
                _bring_foreground(target)
                return True
        # 顺序里没有 force 时补一次，保证至少把窗口显示出来
        if "force" not in self.activation_order:
            ACTIVATORS["force"].trigger(self, hwnd)
        return bool(win32gui.IsWindowVisible(hwnd))

    def __repr__(self) -> str:          # pragma: no cover - 调试用
        return f"<IMClient {self.key}>"


class WeChatClient(IMClient):
    key, name = "wechat", "微信"
    exes = ("weixin.exe", "wechat.exe")
    class_exact = ("WeChatMainWndForPC",)
    class_suffix = ("QWindowIcon",)
    class_prefix = ("Qt",)
    shell_names = ("微信", "WeChat")
    # 实测再启动一次 Weixin.exe 会真的多开一个实例（闪一个窗口再自己退出），
    # 原窗口没反应，所以微信只能靠它自己的托盘入口。
    supports_launch = False
    activation_order = ("shell", "force")


class WeComClient(IMClient):
    key, name = "wecom", "企业微信"
    exes = ("wxwork.exe",)
    class_exact = ("WeWorkWindow",)
    shell_names = ("企业微信", "WeCom")
    # 企微主窗口收托盘时位置仍在屏幕内，直接唤起最稳，也不会点到别的多开账号
    activation_order = ("force", "launch", "shell")


class FeishuClient(IMClient):
    key, name = "feishu", "飞书"
    exes = ("feishu.exe",)
    class_exact = ("Chrome_WidgetWin_1",)
    shell_names = ("飞书", "Feishu", "Lark")


class QQClient(IMClient):
    key, name = "qq", "QQ"
    exes = ("qq.exe",)
    shell_names = ("QQ",)


class DingTalkClient(IMClient):
    key, name = "dingtalk", "钉钉"
    exes = ("dingtalk.exe",)
    # 主框架只有 StandardFrame_DingTalk；Qt 的 QWindowIcon 全是聊天窗/面板，
    # 按后缀匹配会把十几个 0,0 的 374×317 面板算成账号，所以这里只认精确类名。
    class_exact = ("StandardFrame_DingTalk",)
    shell_names = ("钉钉", "DingTalk")
    # 实测钉钉收进托盘后主窗口停在 (0,0,1264,723)，别按"0,0 就是空壳"丢掉
    tray_keeps_zero_rect = True


class IMClientRoster:
    """客户端注册表：按 key 或（进程名, 类名）找客户端。"""

    def __init__(self) -> None:
        self._clients: List[IMClient] = []

    def register(self, client: IMClient) -> IMClient:
        self._clients.append(client)
        return client

    @property
    def clients(self) -> List[IMClient]:
        return list(self._clients)

    def by_key(self, key: Optional[str]) -> Optional[IMClient]:
        if not key:
            return None
        return next((c for c in self._clients if c.key == key), None)

    def match(self, exe: Optional[str], cls: str) -> Optional[IMClient]:
        for client in self._clients:
            if client.matches(exe, cls):
                return client
        return None

    def by_hwnd(self, hwnd: int) -> Optional[IMClient]:
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            exe = _process_name(pid, {})
            return self.match(exe, win32gui.GetClassName(hwnd))
        except Exception:
            return None


ROSTER = IMClientRoster()
for _client in (WeChatClient(), WeComClient(), FeishuClient(), QQClient(), DingTalkClient()):
    ROSTER.register(_client)
CLIENTS: List[IMClient] = ROSTER.clients


def client_by_key(key: Optional[str]) -> Optional[IMClient]:
    return ROSTER.by_key(key)


# ---------------------------------------------------------------------------
# 账号
# ---------------------------------------------------------------------------
@dataclass
class IMAccount:
    client_key: str
    client_name: str
    pid: int
    hwnd: int
    title: str
    class_name: str
    is_foreground: bool
    is_minimized: bool
    unread: bool = False
    is_hidden: bool = False


@dataclass
class _Candidate:
    """枚举阶段的候选窗口，去重后才变成 IMAccount。"""
    hwnd: int
    client: IMClient
    pid: int
    title: str
    class_name: str
    visible: bool
    rect: Tuple[int, int, int, int]
    is_minimized: bool = False

    @property
    def area(self) -> int:
        return max(0, self.rect[2] - self.rect[0]) * max(0, self.rect[3] - self.rect[1])

    def score(self) -> tuple:
        """同一进程里挑主窗口：可见 > 类名精确 > 面积大 > 有位置。"""
        return (self.visible, self.client.is_main_window_class(self.class_name),
                self.area, self.rect[0] != 0 or self.rect[1] != 0)


# ---------------------------------------------------------------------------
# 位置修复
# ---------------------------------------------------------------------------
# 微信/飞书收进托盘会把窗口挪到屏幕外（-32000 之类）并缩成小条。外部直接
# ShowWindow 会把窗口原样显示在屏幕外，这里记住上次的正常矩形再搬回来。
_last_good: Dict[int, Tuple[int, int, int, int]] = {}


def _virtual_screen() -> Tuple[int, int, int, int]:
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))


def _visible_area(rect: Tuple[int, int, int, int]) -> int:
    vx, vy, vw, vh = _virtual_screen()
    w = max(0, min(rect[2], vx + vw) - max(rect[0], vx))
    h = max(0, min(rect[3], vy + vh) - max(rect[1], vy))
    return w * h


def _is_ready(hwnd: int) -> bool:
    """窗口已经"可看可用"：可见、面积正常、位置在屏幕内。"""
    try:
        if not win32gui.IsWindowVisible(hwnd):
            return False
        rect = win32gui.GetWindowRect(hwnd)
    except Exception:
        return False
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    return w >= 400 and h >= 300 and _visible_area(rect) >= 400 * 200


def _needs_restore(hwnd: int) -> bool:
    return not _is_ready(hwnd)


def _wait_ready(hwnd: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.2, timeout)
    while time.monotonic() < deadline:
        if _is_ready(hwnd):
            return True
        time.sleep(0.15)
    return _is_ready(hwnd)


def _client_windows(client: "IMClient", pid: int) -> List[int]:
    """同一客户端进程里，当前可见且像主窗口的那些窗口。"""
    found: List[int] = []
    if not _HAS_WIN32:
        return found
    cache: Dict[int, Optional[str]] = {}
    fg = win32gui.GetForegroundWindow()

    def _enum(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            cls = win32gui.GetClassName(hwnd)
            if not client.is_main_window_class(cls):
                return True
            _, p = win32process.GetWindowThreadProcessId(hwnd)
            if pid and p != pid:
                return True
            if not client.matches(_process_name(p, cache), cls):
                return True
            if _is_ready(hwnd):
                found.append(hwnd)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        return found
    found.sort(key=lambda h: h != fg)      # 前台的那个排前面
    return found


def _wait_ready_target(client: "IMClient", hwnd: int, pid: int, timeout: float) -> int:
    """等窗口真的可用；客户端重建了主窗口时返回新句柄，失败返回 0。"""
    deadline = time.monotonic() + max(0.2, timeout)
    while time.monotonic() < deadline:
        if _is_ready(hwnd):
            return hwnd
        for other in _client_windows(client, pid):
            return other
        time.sleep(0.15)
    if _is_ready(hwnd):
        return hwnd
    others = _client_windows(client, pid)
    return others[0] if others else 0


def _remember_rect(hwnd: int, rect: Tuple[int, int, int, int]) -> None:
    if _visible_area(rect) >= 400 * 300:
        _last_good[hwnd] = rect


def _restore_onscreen(hwnd: int) -> None:
    """把停在屏幕外/被缩成小条的窗口搬回可见区域。

    用 SetWindowPlacement 设置 rcNormalPosition：实测 SetWindowPos 会被
    微信/飞书自己的定位逻辑改回去，而 SetWindowPlacement 它们会接受。
    """
    if not _HAS_WIN32:
        return
    try:
        rect = win32gui.GetWindowRect(hwnd)
    except Exception:
        return
    if _visible_area(rect) >= 400 * 200:
        _remember_rect(hwnd, rect)
        return
    try:
        place = (WINPLACEMENT * 1)()
        ptr = ctypes.cast(place, ctypes.POINTER(WINPLACEMENT))
        ptr.contents.length = ctypes.sizeof(WINPLACEMENT)
        if not user32.GetWindowPlacement(hwnd, ptr):
            return
        p = ptr.contents
        if p.showCmd == 3 or (p.flags & 1):   # 最大化态交给系统还原
            return
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        target = _last_good.get(hwnd)
        if target is None:
            try:
                work = win32api.GetMonitorInfo(
                    win32api.MonitorFromPoint((0, 0), 1))["Work"]
            except Exception:
                work = (0, 0, *_virtual_screen()[2:])
            aw, ah = work[2] - work[0], work[3] - work[1]
            tw = min(max(900, w if w >= 400 else int(aw * 0.72)), aw)
            th = min(max(600, h if h >= 300 else int(ah * 0.78)), ah)
            tx = work[0] + max(0, (aw - tw) // 2)
            ty = work[1] + max(0, (ah - th) // 2)
            target = (tx, ty, tx + tw, ty + th)
        p.rcNormalPosition.left = target[0]
        p.rcNormalPosition.top = target[1]
        p.rcNormalPosition.right = target[2]
        p.rcNormalPosition.bottom = target[3]
        p.showCmd = 1                       # SW_SHOWNORMAL
        p.flags &= ~3                       # 清 WPF_RETORETOMAXIMIZED / SETMINPOSITION
        user32.SetWindowPlacement(hwnd, ptr)
        _remember_rect(hwnd, target)
    except Exception:
        pass


def _window_exe(hwnd: int) -> Optional[str]:
    """窗口对应进程的可执行文件全路径（拿不到返回 None）。"""
    if not _HAS_WIN32:
        return None
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).exe()
    except Exception:
        return None


def _nudge_repaint(hwnd: int) -> None:
    """把窗口尺寸抖 1 像素再改回去，逼客户端重排重绘（不动最大化窗口）。"""
    if not _HAS_WIN32:
        return
    try:
        place = (WINPLACEMENT * 1)()
        ptr = ctypes.cast(place, ctypes.POINTER(WINPLACEMENT))
        ptr.contents.length = ctypes.sizeof(WINPLACEMENT)
        if not user32.GetWindowPlacement(hwnd, ptr):
            return
        if ptr.contents.showCmd == 3:            # 最大化：别抖
            return
        r = win32gui.GetWindowRect(hwnd)
        w, h = r[2] - r[0], r[3] - r[1]
        if w < 400 or h < 300:
            return
        win32gui.SetWindowPos(hwnd, 0, 0, 0, w + 1, h + 1,
                              win32con.SWP_NOMOVE | win32con.SWP_NOZORDER)
        time.sleep(0.06)
        win32gui.SetWindowPos(hwnd, 0, 0, 0, w, h,
                              win32con.SWP_NOMOVE | win32con.SWP_NOZORDER)
    except Exception:
        pass


def foreground_root() -> int:
    """当前前台窗口的顶层窗口句柄（找不到返回 0）。"""
    if not _HAS_WIN32:
        return 0
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return 0
        root = win32gui.GetAncestor(hwnd, 2)  # GA_ROOT
        return root or hwnd
    except Exception:
        return 0


def _is_foreground(hwnd: int) -> bool:
    try:
        fg = win32gui.GetForegroundWindow()
        if not fg:
            return False
        return fg == hwnd or win32gui.GetAncestor(fg, 2) == hwnd
    except Exception:
        return False


def _bring_foreground(hwnd: int) -> bool:
    """只把前台交给目标窗口，不改位置、不重排 z 序。"""
    if _is_foreground(hwnd):
        return True
    for attempt in range(3):
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        if _is_foreground(hwnd):
            return True
        _alt_tap()
        time.sleep(0.1)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    return _is_foreground(hwnd)


def _alt_tap() -> None:
    """轻敲一下 Alt，松开 Windows 的前台锁，让 SetForegroundWindow 生效。"""
    try:
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 0x02, 0)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 唤起方式
# ---------------------------------------------------------------------------
class Activator:
    """一种唤起手段。trigger() 只负责"触发一次"，是否到位由调用方轮询判定。"""

    def trigger(self, client: IMClient, hwnd: int) -> bool:   # pragma: no cover
        raise NotImplementedError


class LaunchActivator(Activator):
    """像双击快捷方式那样再启动一次客户端。

    单实例客户端（企微/飞书/QQ/钉钉）会把这次启动请求转给已在运行的实例，
    由它自己走一遍"显示主窗口"流程：重建画面、恢复焦点、摆回正常位置。
    全程不碰任务栏和托盘，所以不会先弹出右下角的隐藏图标面板。
    """

    COOLDOWN = 1.5

    def __init__(self) -> None:
        self._last: Dict[str, float] = {}

    def trigger(self, client: IMClient, hwnd: int) -> bool:
        if not client.supports_launch or not _HAS_WIN32:
            return False
        now = time.monotonic()
        if now - self._last.get(client.key, 0.0) < self.COOLDOWN:
            return False                    # 连点保护：别一次点出两个进程
        exe = _window_exe(hwnd)
        if not exe or not os.path.isfile(exe):
            return False
        try:
            subprocess.Popen(
                [exe], cwd=os.path.dirname(exe), close_fds=True,
                creationflags=0x08000000,       # CREATE_NO_WINDOW
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return False
        self._last[client.key] = now
        return True


class ForceActivator(Activator):
    """外部强制显示：还原位置 → 显示 → 置顶往返 → 前台（失败时敲 Alt）。

    只在"客户端自己的入口"全部失败时作为兜底。微信/飞书在托盘态被外部
    ShowWindow 拉起来时，画面可能是停住的一帧，所以这里显示完再做一次
    一像素的尺寸抖动，逼客户端重排重绘。
    """

    def trigger(self, client: IMClient, hwnd: int) -> bool:
        if not _HAS_WIN32 or not win32gui.IsWindow(hwnd):
            return False
        _restore_onscreen(hwnd)
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
            _restore_onscreen(hwnd)
            _nudge_repaint(hwnd)
            flag = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flag)
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flag)
            win32gui.BringWindowToTop(hwnd)
        except Exception:
            return False
        for attempt in range(3):
            if _is_foreground(hwnd):
                break
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            if _is_foreground(hwnd):
                break
            if attempt < 2:
                _alt_tap()
            time.sleep(0.12)
        return bool(win32gui.IsWindowVisible(hwnd))


class ShellActivator(Activator):
    """系统那一套：点任务栏按钮 / 托盘子图标，让客户端自己把窗口显示出来。

    微信 4.x、飞书收托盘时会把主窗口挪出屏幕并让内部"显示流程"停住，
    外部 ShowWindow 只能搬回一张点不动的旧画面；而任务栏/托盘入口是客户端
    自己的入口，它会连同画面与激活状态一起准备好。

    同一个"客户端自己的入口"其实有三档，哪一档有效取决于窗口此刻的状态：
        窗口在托盘（不可见/离屏） → 托盘露出的图标 → 隐藏图标浮层 → 任务栏按钮
        窗口本来就可见           → 任务栏按钮 → 托盘图标
    每档点完都轮询验证，没出来就换下一档，全部失败才交给 force。
    """

    STEP_TIMEOUT = 1.3

    def trigger(self, client: IMClient, hwnd: int) -> bool:
        auto = _uia()
        if auto is None or not client.shell_names:
            return False
        bars = _taskbar_windows()
        if not bars:
            return False
        if _is_ready(hwnd):
            steps = (self.taskbar_button, self.tray_shown, self.tray_overflow)
        else:
            # 浮层会闪一下右下角，所以排最后：托盘里直接露出的图标和任务栏
            # 按钮都看不出动静，只有它们都不好使时才展开浮层。
            steps = (self.tray_shown, self.taskbar_button, self.tray_overflow)
        for step in steps:
            try:
                fired = step(auto, bars, client)
            except Exception:
                fired = False
            if not fired:
                continue
            if _wait_ready(hwnd, self.STEP_TIMEOUT):
                return True
        return False

    # ---- 三档入口 ------------------------------------------------------
    def taskbar_button(self, auto, bars, client) -> bool:
        """任务栏上已经固定的客户端（飞书/微信常见）。"""
        buttons: List = []
        for bar in bars:
            try:
                _collect_buttons(auto.ControlFromHandle(bar), client.shell_names,
                                 buttons, tray_only=False)
            except Exception:
                continue
        return _invoke(buttons[0]) if buttons else False

    def tray_shown(self, auto, bars, client) -> bool:
        """托盘里直接露出来的图标（没被收进「显示隐藏的图标」）。"""
        for bar in bars:
            try:
                shown: List = []
                _collect_buttons(auto.ControlFromHandle(bar), client.shell_names,
                                 shown, tray_only=True)
            except Exception:
                continue
            if shown:
                return _invoke(shown[0])
        return False

    def tray_overflow(self, auto, bars, client) -> bool:
        """图标被收进「显示隐藏的图标」浮层时：展开 → 点图标 → 收起。"""
        toggles: List = []
        for bar in bars:
            try:
                toggle = _find_overflow_toggle(auto.ControlFromHandle(bar))
            except Exception:
                toggle = None
            if toggle is not None:
                toggles.append(toggle)
                break
        if not toggles:
            return False
        was_open = _overflow_open()
        if not was_open and not _invoke(toggles[0]):
            return False
        icons: List = []
        for _ in range(9):                  # 浮层是异步弹出的，轮询拿图标
            time.sleep(0.14)
            icons = _overflow_icons(client.shell_names)
            if icons:
                break
        clicked = _invoke(icons[0]) if icons else False
        if not was_open:
            _close_overflow(toggles[0])
        return clicked


ACTIVATORS: Dict[str, Activator] = {
    "launch": LaunchActivator(),
    "force": ForceActivator(),
    "shell": ShellActivator(),
}


# ---------------------------------------------------------------------------
# 系统 UI（任务栏/托盘）辅助
# ---------------------------------------------------------------------------
_uia_mod = None


def _uia():
    global _uia_mod
    if _uia_mod is None:
        try:
            import uiautomation as auto
            _uia_mod = auto
        except Exception:
            _uia_mod = False
    return _uia_mod or None


def _taskbar_windows() -> List[int]:
    found: List[int] = []

    def enum(hwnd, _):
        try:
            if win32gui.GetClassName(hwnd) in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                found.append(hwnd)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(enum, None)
    except Exception:
        pass
    return found


def _collect_buttons(ctrl, names, out: List, tray_only: bool, depth: int = 0) -> None:
    if depth > 12 or len(out) >= 4:
        return
    try:
        cls = ctrl.ClassName or ""
        name = (ctrl.Name or "").strip()      # 托盘图标的 Name 常带前导空格
    except Exception:
        return
    if name and any(name.startswith(n) for n in names):
        if tray_only and "SystemTray" in cls:
            out.append(ctrl)
        elif not tray_only and "TaskListButton" in cls:
            out.append(ctrl)
    try:
        for child in ctrl.GetChildren():
            _collect_buttons(child, names, out, tray_only, depth + 1)
    except Exception:
        return


def _find_overflow_toggle(root):
    """托盘里那个"显示隐藏的图标"按钮（用于展开/收起隐藏图标浮层）。"""
    hit: List = []

    def walk(ctrl, depth: int = 0):
        if depth > 12 or hit:
            return
        try:
            name = ctrl.Name or ""
            cls = ctrl.ClassName or ""
        except Exception:
            return
        if "SystemTray" in cls and ("显示隐藏" in name or "Hidden" in name):
            hit.append(ctrl)
            return
        try:
            for child in ctrl.GetChildren():
                walk(child, depth + 1)
        except Exception:
            return

    walk(root)
    return hit[0] if hit else None


def _overflow_icons(names) -> List:
    """隐藏图标浮层里属于这个客户端的托盘图标控件。"""
    auto = _uia()
    out: List = []
    if auto is None:
        return out
    hwnd = _overflow_hwnd()
    if not hwnd:
        return out
    try:
        _collect_buttons(auto.ControlFromHandle(hwnd), names, out, tray_only=True)
    except Exception:
        return out
    return out


def _overflow_hwnd() -> int:
    """「显示隐藏的图标」浮层的窗口句柄（原生枚举，比走 UIA 根节点快一个量级）。"""
    found: List[int] = []

    def _enum(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            cls = win32gui.GetClassName(hwnd)
        except Exception:
            return True
        if "Overflow" in cls:
            r = win32gui.GetWindowRect(hwnd)
            if r[2] - r[0] > 0 and r[3] - r[1] > 0:
                found.append(hwnd)
        return True

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        return 0
    return found[0] if found else 0


def _overflow_open() -> bool:
    """托盘的“隐藏图标”浮层现在是不是开着。"""
    return bool(_overflow_hwnd())


def _close_overflow(toggle=None) -> None:
    """把隐藏图标浮层收回去，免得它继续占着前台。

    大多数时候点完图标浮层自己就收了，所以先看一眼：还开着才动它，
    免得对已经收起的浮层再点一次又把它打开。
    """
    if not _overflow_open():
        return
    if toggle is not None:
        _invoke(toggle)
        time.sleep(0.15)
        if not _overflow_open():
            return
    try:
        user32.keybd_event(0x1B, 0, 0, 0)     # ESC
        user32.keybd_event(0x1B, 0, 0x02, 0)
    except Exception:
        pass


def _invoke(ctrl) -> bool:
    try:
        ctrl.GetInvokePattern().Invoke()
        return True
    except Exception:
        pass
    try:
        ctrl.GetLegacyIAccessiblePattern().DoDefaultAction()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------
def _process_name(pid: int, cache: Dict[int, Optional[str]]) -> Optional[str]:
    if pid in cache:
        return cache[pid]
    name: Optional[str] = None
    if psutil is not None:
        try:
            name = psutil.Process(pid).name()
        except Exception:
            name = None
    cache[pid] = name
    return name


def _is_ghost(c: _Candidate) -> bool:
    """隐藏且正好停在 (0,0) 的窗口：客户端建好但从未显示过的空壳。

    注意不能按"左上角 <= 0"判断：最大化窗口的矩形是 (-7,-7,1543,823)。
    真正的托盘窗口会被挪到 -32000 一类的位置（或留在原处），不会正好是原点，
    而企微这类客户端会在启动时留下多个 (0,0,1475,950) 的辅助窗口，
    以前就是它们被多算成了"多余的账号"。
    """
    if c.visible:
        return False
    if c.client.tray_keeps_zero_rect:
        return False          # 这类客户端收托盘时就停在原点，主窗口类名不会误判
    return c.rect[0] == 0 and c.rect[1] == 0


def _dedupe(candidates: List[_Candidate]) -> List[_Candidate]:
    """按「一个进程一个主窗口」收敛，去掉空壳与辅助窗口。"""
    kept: List[_Candidate] = []
    by_process: Dict[int, List[_Candidate]] = {}
    for c in candidates:
        by_process.setdefault(c.pid, []).append(c)
    for pid, group in by_process.items():
        client = group[0].client
        alive = [c for c in group if not _is_ghost(c)]
        alive.sort(key=lambda c: c.score(), reverse=True)
        kept.extend(alive[:max(1, client.accounts_per_process)])
    kept.sort(key=lambda c: (c.client.name, c.title, c.pid))
    return kept


def list_accounts(enabled: Dict[str, bool]) -> List[IMAccount]:
    """枚举各客户端的主窗口，含已收进托盘（不可见）的窗口。

    一个进程只保留一个主窗口，所以多开账号各占一行、单账号不会多出空壳行。
    """
    if not _HAS_WIN32:
        return []
    candidates: List[_Candidate] = []
    pid_cache: Dict[int, Optional[str]] = {}
    fg_hwnd = win32gui.GetForegroundWindow()

    def _enum(hwnd, _):
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return True
        cls = win32gui.GetClassName(hwnd)
        if cls.lower() in _CLASS_BLOCKLIST:
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            return True
        exe = _process_name(pid, pid_cache)
        client = ROSTER.match(exe, cls)
        if client is None or not enabled.get(client.key, False):
            return True

        visible = bool(win32gui.IsWindowVisible(hwnd))
        if visible:
            _remember_rect(hwnd, rect)
        elif not client.is_main_window_class(cls):
            return True                     # 隐藏窗口只认主窗口类名
        if rect[2] - rect[0] < 200 or rect[3] - rect[1] < 200:
            return True
        candidates.append(_Candidate(
            hwnd=hwnd, client=client, pid=pid, title=title, class_name=cls,
            visible=visible, rect=rect, is_minimized=bool(win32gui.IsIconic(hwnd)),
        ))
        return True

    win32gui.EnumWindows(_enum, None)
    accounts = [
        IMAccount(
            client_key=c.client.key,
            client_name=c.client.name,
            pid=c.pid,
            hwnd=c.hwnd,
            title=c.title,
            class_name=c.class_name,
            is_foreground=(c.hwnd == fg_hwnd),
            is_minimized=c.is_minimized,
            is_hidden=not c.visible,
        )
        for c in _dedupe(candidates)
    ]
    return accounts


def focus_account(hwnd: int, client_key: Optional[str] = None) -> bool:
    """把指定账号窗口恢复到可用状态并切到前台。

    client_key 用来选客户端的唤起策略；不传就按窗口自己反查。
    """
    client = client_by_key(client_key) if client_key else None
    if client is None:
        client = ROSTER.by_hwnd(hwnd)
    if client is not None:
        return client.activate(hwnd)
    return ACTIVATORS["force"].trigger(None, hwnd)   # type: ignore[arg-type]


def raise_window(hwnd: int) -> bool:
    """兼容旧调用：单纯强制显示并激活（不区分客户端）。"""
    return ACTIVATORS["force"].trigger(None, hwnd)   # type: ignore[arg-type]


def hide_window(hwnd: int) -> None:
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
    except Exception:
        pass


def is_blank(hwnd: int) -> bool:
    """窗口显示着、却一片单色 —— 被强行 Show 但没真正画出来的空壳窗。"""
    if not _HAS_WIN32 or not win32gui.IsWindowVisible(hwnd):
        return False
    try:
        r = win32gui.GetWindowRect(hwnd)
    except Exception:
        return False
    w, h = r[2] - r[0], r[3] - r[1]
    if w < 300 or h < 200:
        return False

    rect = (r[0] + 30, r[1] + 12, min(260, w - 60), 30)
    x, y, sw, sh = rect
    hdc = user32.GetDC(None)
    if not hdc:
        return False
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, sw, sh)
    buf = ctypes.create_string_buffer(sw * sh * 4)
    try:
        gdi32.SelectObject(mdc, bmp)
        if not gdi32.BitBlt(mdc, 0, 0, sw, sh, hdc, x, y, 0x00CC0020):
            return False
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth = sw
        bi.biHeight = -sh
        bi.biPlanes = 1
        bi.biBitCount = 32
        if not gdi32.GetDIBits(mdc, bmp, 0, sh, buf, ctypes.byref(bi), 0):
            return False
        data = buf.raw
        lo = 255
        hi = 0
        for i in range(0, len(data), 4):
            v = (data[i] + data[i + 1] + data[i + 2]) // 3
            if v < lo:
                lo = v
            if v > hi:
                hi = v
        return (hi - lo) <= 3
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(None, hdc)
