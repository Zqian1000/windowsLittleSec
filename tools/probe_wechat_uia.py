"""看看微信窗口的 UIA 树是否能暴露会话/未读信息（决定后续要不要走这条路）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psutil  # noqa: E402
import uiautomation as auto  # noqa: E402
import win32gui  # noqa: E402
import win32process  # noqa: E402


def find_wechat():
    found = []

    def enum(hwnd, _):
        if win32gui.GetWindowText(hwnd) != "微信":
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name().lower()
        except Exception:
            return
        if name == "weixin.exe":
            found.append(hwnd)

    win32gui.EnumWindows(enum, None)
    return found[0] if found else 0


hwnd = find_wechat()
print("hwnd:", hwnd)
ctrl = auto.ControlFromHandle(hwnd)
counter = 0


def walk(c, depth=0):
    global counter
    if depth > 4 or counter > 60:
        return
    try:
        print("  " * depth + f"{c.ControlTypeName} cls={c.ClassName!r} name={c.Name!r}")
    except Exception:
        return
    counter += 1
    try:
        for ch in c.GetChildren():
            walk(ch, depth + 1)
    except Exception:
        return


walk(ctrl)
print("nodes:", counter)
