"""列出任务栏与托盘的所有子窗口（含溢出面板），看图标到底画在哪个窗口上。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32gui  # noqa: E402


def dump(root_hwnd, title):
    if not root_hwnd:
        print(f"{title}: not found")
        return
    print(f"\n=== {title} hwnd={root_hwnd} class={win32gui.GetClassName(root_hwnd)} ===")
    rows = []

    def child(hwnd, _):
        try:
            cls = win32gui.GetClassName(hwnd)
            txt = win32gui.GetWindowText(hwnd)
            rect = win32gui.GetWindowRect(hwnd)
            vis = bool(win32gui.IsWindowVisible(hwnd))
            rows.append((hwnd, cls, txt, rect, vis))
        except Exception:
            pass
        return True

    win32gui.EnumChildWindows(root_hwnd, child, None)
    for hwnd, cls, txt, rect, vis in rows:
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        if w <= 0 or h <= 0:
            continue
        print(f"  hwnd={hwnd:>10} cls={cls[:38]:38} vis={vis} "
              f"rect=({rect[0]},{rect[1]},{w},{h}) text={txt!r}")


dump(win32gui.FindWindow("Shell_TrayWnd", None), "Shell_TrayWnd")
dump(win32gui.FindWindow("NotifyIconOverflowWindow", None), "NotifyIconOverflowWindow")
