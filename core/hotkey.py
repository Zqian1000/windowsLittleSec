"""全局热键（RegisterHotKey），给截图这类"随时要按"的动作挂快捷键。

用一个隐藏的 QWidget 收 WM_HOTKEY：注册时把 HWND 交给系统，消息只会
派到这个窗口，再经 QApplication 的 native event filter 拿到，不必上键盘钩子。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import itertools
from typing import Optional

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, Signal
from PySide6.QtWidgets import QWidget

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

_MODS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "meta": MOD_WIN, "cmd": MOD_WIN,
}
_VK = {f"f{i}": 0x6F + i for i in range(1, 25)}   # F1=0x70
_VK.update({
    "printscreen": 0x2C, "print": 0x2C, "insert": 0x2D,
    "space": 0x20, "enter": 0x0D, "esc": 0x1B,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, ";": 0xBA, "'": 0xDE,
    ",": 0xBC, ".": 0xBE, "/": 0xBF,
})

_counter = itertools.count(0x5100)


def parse(spec: str) -> Optional[tuple[int, int]]:
    """'ctrl+alt+x' → (修饰键, 虚拟键码)；写错返回 None。"""
    parts = [p.strip().lower() for p in (spec or "").split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    key = ""
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        elif p in _VK:
            key = p
        elif len(p) == 1 and (p.isalpha() or p.isdigit()):
            key = p
        else:
            return None
    if not key:
        return None
    vk = _VK[key] if key in _VK else ord(key.upper())
    return mods, vk


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, hotkey: "GlobalHotkey") -> None:
        super().__init__()
        self._hotkey = hotkey

    def nativeEventFilter(self, eventType, message):
        try:
            kind = bytes(eventType).decode("ascii", "ignore") if eventType else ""
        except Exception:
            kind = str(eventType)
        if "windows" not in kind:
            return False, 0
        try:
            msg = wt.MSG.from_address(int(message))
        except Exception:
            return False, 0
        if msg.message == WM_HOTKEY and msg.wParam == self._hotkey.ident:
            self._hotkey.triggered.emit()
            return True, 0
        return False, 0


class GlobalHotkey(QObject):
    triggered = Signal()

    def __init__(self, spec: str, parent=None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.ident = next(_counter)
        self._parsed = parse(spec)
        self.ok = False
        self._widget: Optional[QWidget] = None
        self._filter: Optional[_Filter] = None

    def register(self) -> bool:
        if self._parsed is None or self.ok:
            return self.ok
        mods, vk = self._parsed
        # 收热键的窗口：不显示、不抢焦点，只为拿消息
        self._widget = QWidget(None)
        self._widget.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self._widget.setAttribute(Qt.WA_DontShowOnScreen, True)
        self._widget.resize(1, 1)
        hwnd = int(self._widget.winId())
        if not ctypes.windll.user32.RegisterHotKey(hwnd, self.ident, mods, vk):
            self._widget.deleteLater()
            self._widget = None
            return False
        self._filter = _Filter(self)
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)
        self.ok = True
        return True

    def unregister(self) -> None:
        if not self.ok:
            return
        try:
            hwnd = int(self._widget.winId()) if self._widget else 0
            ctypes.windll.user32.UnregisterHotKey(hwnd, self.ident)
        except Exception:
            pass
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None and self._filter is not None:
            app.removeNativeEventFilter(self._filter)
        self.ok = False
