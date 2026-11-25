"""验证通知窗信号的新语义：只认新出现的窗，常驻辅助窗不点亮。"""
import ctypes, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.unread import FlashWatcher
import win32gui, win32process


def find_owner(title):
    found = []

    def cb(h, _):
        try:
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == title:
                found.append(win32process.GetWindowThreadProcessId(h)[1])
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


CHILD = r'''
import tkinter as tk, sys
title, x, y = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
r = tk.Tk()
r.title(title)
r.geometry(f"300x120+{x}+{y}")
r.overrideredirect(True)
r.attributes("-topmost", True)
r.after(25000, r.destroy)
r.mainloop()
'''
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_toast_child.py")
with open(path, "w", encoding="utf-8") as f:
    f.write(CHILD)

enabled = {"wechat": True}
fg = {}

w = FlashWatcher()
print("first poll (priming, toast 未出现):", w.poll(enabled, fg))

# 先造一个“常驻辅助窗”，模拟客户端一直在的提示窗
persist = subprocess.Popen([sys.executable, path, "常驻提示窗", "900", "400"])
time.sleep(1.8)
owner_p = find_owner("常驻提示窗")
w._pids = {owner_p: "wechat"}
w._pid_time = time.monotonic()
print("poll with persistent window:", w.poll(enabled, fg))

toast = subprocess.Popen([sys.executable, path, "张三: 在吗", "1000", "500"])
time.sleep(1.8)
owner = find_owner("张三: 在吗")
w._pids = {owner: "wechat", owner_p: "wechat"}
w._pid_time = time.monotonic()
print("after new toast appears:", w.poll(enabled, fg))
print("second poll (仍在 30s 保持期):", w.poll(enabled, fg))
w.clear("wechat")
print("after clear (常驻窗还在，不应再亮):", w.poll(enabled, fg))

for p in (toast, persist):
    try:
        p.kill()
    except Exception:
        pass
try:
    os.remove(path)
except OSError:
    pass
