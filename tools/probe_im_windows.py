import ctypes
from ctypes import wintypes
import win32gui, win32process, win32con
u = ctypes.windll.user32
u.SetProcessDPIAware()
target_pids = {}
for name in ("Weixin.exe", "WXWork.exe", "Feishu.exe"):
    pass
import psutil
pids = {}
for p in psutil.process_iter(["name", "pid"]):
    n = (p.info["name"] or "").lower()
    if n in ("weixin.exe", "wxwork.exe", "feishu.exe"):
        pids[p.info["pid"]] = n
print("im pids:", pids)
rows = []
def cb(hwnd, _):
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value not in pids:
        return True
    cls = win32gui.GetClassName(hwnd)
    title = win32gui.GetWindowText(hwnd)
    vis = bool(u.IsWindowVisible(hwnd))
    r = win32gui.GetWindowRect(hwnd)
    rows.append((pids[pid.value], hwnd, cls, title, vis, r[2]-r[0], r[3]-r[1], r))
    return True
u.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
for row in sorted(rows, key=lambda x: (x[0], -x[6])):
    print(row)
