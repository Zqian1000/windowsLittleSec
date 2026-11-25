"""跨进程像素对照：微信任务栏按钮在闪动前后的颜色变化。"""
import ctypes
import time

import uiautomation as auto
import win32gui
import win32process
import psutil
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
import sys

user32 = ctypes.windll.user32


class FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint), ("uCount", ctypes.c_uint),
                ("dwTimeout", ctypes.c_uint)]


def find_wechat():
    found = []

    def enum(hwnd, _):
        if win32gui.GetWindowText(hwnd) != "微信":
            return
        cls = win32gui.GetClassName(hwnd)
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name().lower()
        except Exception:
            return
        if name == "weixin.exe" and cls.startswith("Qt"):
            found.append(hwnd)

    win32gui.EnumWindows(enum, None)
    return found[0] if found else 0


def find_wechat_button():
    found = []

    def walk(ctrl, depth=0):
        if depth > 14 or found:
            return
        try:
            cls = ctrl.ClassName or ""
        except Exception:
            return
        if "TaskListButton" in cls:
            try:
                name = ctrl.Name or ""
                if name.startswith("微信") and "企业微信" not in name:
                    found.append(ctrl)
                    return
            except Exception:
                pass
        try:
            for c in ctrl.GetChildren():
                walk(c, depth + 1)
        except Exception:
            return

    walk(auto.GetRootControl())
    return found[0] if found else None


def mean_color(rect):
    screen = QGuiApplication.primaryScreen()
    img = screen.grabWindow(0, int(rect.left), int(rect.top),
                            max(1, int(rect.width())), max(1, int(rect.height())),
                            ).toImage()
    if img.isNull():
        return None
    tr = tg = tb = 0
    n = 0
    for y in range(2, img.height() - 2, 2):
        for x in range(2, img.width() - 2, 2):
            c = img.pixelColor(x, y)
            tr += c.red(); tg += c.green(); tb += c.blue(); n += 1
    return (tr / n, tg / n, tb / n) if n else None


app = QApplication(sys.argv)
hwnd = find_wechat()
print("wechat hwnd:", hwnd)
btn = find_wechat_button()
print("button:", btn.Name if btn else None)
if btn is None:
    print("VERDICT: BUTTON_NOT_FOUND")
    sys.exit(0)

rect = btn.BoundingRectangle
print("rect:", rect)
vals = []


def sample(tag):
    m = mean_color(rect)
    if m:
        vals.append((tag, m))
        print(f"  {tag}: ({m[0]:.1f},{m[1]:.1f},{m[2]:.1f})")


sample("off1"); sample("off2"); sample("off3")
user32.FlashWindowEx(ctypes.byref(FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 3, 30, 0)))
for i in range(10):
    time.sleep(0.15)
    sample(f"on{i}")
user32.FlashWindowEx(ctypes.byref(FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0, 0, 0)))

off = [m for t, m in vals if t.startswith("off")]
on = [m for t, m in vals if t.startswith("on")]
if off and on:
    # 用红通道的极差衡量是否出现明暗交替
    span = max(m[0] for m in on) - min(m[0] for m in on)
    print(f"on red span={span:.1f}")
    print("VERDICT:", "PIXEL_CHANGES" if span > 4 else "PIXEL_STABLE")
