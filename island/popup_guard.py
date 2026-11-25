"""统一的浮层自动收起规则。

为什么要单独做一层：`Qt.Tool` 浮层在 Windows 上不一定拿得到激活，靠
`WindowDeactivate` 时灵时不灵；而单纯比对前台进程又会误伤——用户点完岛上的
按钮、卡片刚弹出来，前台可能还停在 Excel，卡片就被判成"没人要"收掉了。

现在只看两件确定的事：
1. 在本程序任何窗口之外按下鼠标（左键或右键）→ 收；
2. 前台窗口真的换成了别的进程（Alt-Tab / 点任务栏）→ 收。
浮层自己露着不动、鼠标只是移开，都不算"离开"，不会自己消失。
钉住的（`pinned=True`）和声明 `auto_dismiss=False` 的（工具箱）不收。
"""
from __future__ import annotations

import ctypes
import os
import time
from typing import Callable, Iterable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QWidget

# 浮层刚露出来的这一小段里，Windows 可能还在岛/原窗口/浮层之间来回倒焦点，
# 这期间只记录前台，不据此下判断，避免"刚点开就消失"。
SETTLE_SEC = 1.2

_user32 = ctypes.windll.user32
_user32.GetForegroundWindow.restype = ctypes.c_void_p
_user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]

LBUTTON = 0x01
RBUTTON = 0x02


def _foreground_hwnd() -> int:
    return int(_user32.GetForegroundWindow() or 0)


def _hwnd_pid(hwnd: int) -> int:
    pid = ctypes.c_ulong()
    _user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    return pid.value


def _inside_ours(pos) -> bool:
    """点位是否落在本程序任一可见窗口里（岛、浮层都算）。"""
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QWidget) and w.isVisible() and w.frameGeometry().contains(pos):
            return True
    return False


class PopupGuard(QObject):
    """盯着鼠标和前台窗口，把"用户去忙别的了"翻译成浮层 hide()。"""

    def __init__(self, provider: Callable[[], Iterable[QWidget]], parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._fg = _foreground_hwnd()
        self._pressed = False
        self._seen: set[int] = set()
        self._quiet_until = 0.0

        self._mouse_timer = QTimer(self)
        self._mouse_timer.setInterval(50)      # 够快，正常点击不会漏
        self._mouse_timer.timeout.connect(self._check_click)
        self._mouse_timer.start()

        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(250)
        self._focus_timer.timeout.connect(self._check_focus)
        self._focus_timer.start()

    # ---- 规则 ----
    def _popups(self) -> list[QWidget]:
        try:
            items = list(self._provider() or ())
        except Exception:
            return []
        out: list[QWidget] = []
        for w in items:
            if w is None or not w.isVisible():
                continue
            if getattr(w, "pinned", False):
                continue
            if getattr(w, "auto_dismiss", True) is False:
                continue
            out.append(w)
        return out

    def _refresh(self) -> list[QWidget]:
        """刷新可见集合；有新浮层冒出来就进入安定期。"""
        ws = self._popups()
        ids = {id(w) for w in ws}
        if ids - self._seen:
            self._quiet_until = time.monotonic() + SETTLE_SEC
        self._seen = ids
        return ws

    def dismiss(self) -> None:
        for w in self._popups():
            w.hide()

    def _check_click(self) -> None:
        down = bool(_user32.GetAsyncKeyState(LBUTTON) & 0x8000) or \
            bool(_user32.GetAsyncKeyState(RBUTTON) & 0x8000)
        self._handle_press(down, QCursor.pos())

    def _handle_press(self, down: bool, pos) -> None:
        """按下沿 + 点位：在外面的第一下就收。拆出来是为了能单独测。"""
        if down and not self._pressed and self._refresh():
            if not _inside_ours(pos):
                self.dismiss()
        self._pressed = down

    def _check_focus(self) -> None:
        self._refresh()
        hwnd = _foreground_hwnd()
        if hwnd == self._fg:
            return
        self._fg = hwnd
        if time.monotonic() < self._quiet_until:
            return
        if hwnd and _hwnd_pid(hwnd) != os.getpid():
            self.dismiss()
