"""逐句诊断 focus_account 内部每一步是否成功。"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32con  # noqa: E402
import win32gui  # noqa: E402
import win32process  # noqa: E402

from core import config, im  # noqa: E402

accounts = im.list_accounts({c.key: True for c in im.CLIENTS})
target = next((a for a in accounts if a.client_key == "wechat"), None)
if not target:
    print("no wechat")
    sys.exit(1)
hwnd = target.hwnd
print("hwnd:", hwnd, "IsWindow:", win32gui.IsWindow(hwnd))


def step(name, fn):
    try:
        r = fn()
        print(f"  {name}: ok -> {r}")
        return True
    except Exception as exc:
        print(f"  {name}: FAIL {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return False


step("IsIconic", lambda: win32gui.IsIconic(hwnd))
step("ShowWindow SW_SHOW", lambda: win32gui.ShowWindow(hwnd, win32con.SW_SHOW))
time.sleep(0.4)
print("  visible:", bool(win32gui.IsWindowVisible(hwnd)), "rect:", win32gui.GetWindowRect(hwnd))

fg = win32gui.GetForegroundWindow()
fg_tid = win32process.GetWindowThreadProcessId(fg)[0]
cur_tid = win32process.GetCurrentThreadId()
print("  fg:", fg, "fg_tid:", fg_tid, "cur_tid:", cur_tid)
step("AttachThreadInput(True)", lambda: win32process.AttachThreadInput(cur_tid, fg_tid, True))
step("ShowWindow SW_RESTORE", lambda: win32gui.ShowWindow(hwnd, win32con.SW_RESTORE))
step("BringWindowToTop", lambda: win32gui.BringWindowToTop(hwnd))
step("SetForegroundWindow", lambda: win32gui.SetForegroundWindow(hwnd))
step("AttachThreadInput(False)", lambda: win32process.AttachThreadInput(cur_tid, fg_tid, False))
time.sleep(1.0)
now = win32gui.GetForegroundWindow()
print("  foreground now:", now, "target?", now == hwnd,
      "visible:", bool(win32gui.IsWindowVisible(hwnd)))
