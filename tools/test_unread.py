"""未读检测联调：先确认无假阳性，再对微信窗口触发闪动看是否能点亮。"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psutil  # noqa: E402
import win32gui  # noqa: E402
import win32process  # noqa: E402

from core.unread import FlashWatcher  # noqa: E402


class FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint), ("uCount", ctypes.c_uint),
                ("dwTimeout", ctypes.c_uint)]


user32 = ctypes.windll.user32


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
        if name == "weixin.exe" and win32gui.GetClassName(hwnd).startswith("Qt"):
            found.append(hwnd)

    win32gui.EnumWindows(enum, None)
    return found[0] if found else 0


w = FlashWatcher()
enabled = {"wechat": True, "qq": True, "wecom": True,
           "feishu": True, "dingtalk": True}
foreground = {}

t0 = time.monotonic()
w.poll(enabled, foreground)
print(f"locate took {time.monotonic() - t0:.2f}s")
for key in enabled:
    print(f"  {key:8} taskbar={w._taskbar.get(key)} tray={w._tray.get(key)}")

print("\n--- 静置 2.4s（应为全 False）---")
clean = True
for _ in range(8):
    st = w.poll(enabled, foreground)
    if any(st.values()):
        clean = False
    print(" ", {k: v for k, v in st.items() if v} or "none")
    time.sleep(0.3)
print("no false positive:", clean)

hwnd = find_wechat()
print("\n--- 闪动 wechat hwnd:", hwnd, "---")
hit = False
if hwnd:
    start = time.monotonic()
    while time.monotonic() - start < 6:
        user32.FlashWindowEx(ctypes.byref(
            FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 3, 60, 0)))
        st = w.poll(enabled, foreground)
        if st.get("wechat"):
            hit = True
            print(f"  detected after {time.monotonic() - start:.1f}s")
            break
        time.sleep(0.3)
    user32.FlashWindowEx(ctypes.byref(
        FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0, 0, 0)))
print("flash detected:", hit)
print("after stop:", w.poll(enabled, foreground))
