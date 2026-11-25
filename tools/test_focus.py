"""诊断：对微信窗口调用 focus_account，检查前台窗口是否真的切过去。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32gui  # noqa: E402

from core import config, im  # noqa: E402

cfg = config.load()
enabled = {c.key: True for c in im.CLIENTS}
accounts = im.list_accounts(enabled)
print("accounts:")
for a in accounts:
    print(f"  {a.client_key:8} hwnd={a.hwnd} title={a.title!r} hidden={a.is_hidden} "
          f"min={a.is_minimized} pid={a.pid}")

fg_before = win32gui.GetForegroundWindow()
print("foreground before:", fg_before, repr(win32gui.GetWindowText(fg_before)))

target = next((a for a in accounts if a.client_key == "wechat"), None)
if target is None:
    print("no wechat account")
    sys.exit(1)

ok = im.focus_account(target.hwnd)
time.sleep(1.2)
fg_after = win32gui.GetForegroundWindow()
print("focus_account returned:", ok)
print("foreground after:", fg_after, repr(win32gui.GetWindowText(fg_after)),
      "is_target:", fg_after == target.hwnd)
print("target visible now:", bool(win32gui.IsWindowVisible(target.hwnd)),
      "iconic:", bool(win32gui.IsIconic(target.hwnd)))
print("target rect:", win32gui.GetWindowRect(target.hwnd))
