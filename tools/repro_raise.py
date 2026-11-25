"""按用户路径复现：微信收托盘 → 点岛图标唤起 → 测点击/滚轮是否还响应。"""
import ctypes
import time
from ctypes import wintypes

import psutil
import win32con
import win32gui
import win32process

u = ctypes.windll.user32
gdi = ctypes.windll.gdi32
u.SetProcessDPIAware()
u.GetDC.restype = ctypes.c_void_p
u.GetDC.argtypes = [ctypes.c_void_p]
gdi.CreateCompatibleDC.restype = ctypes.c_void_p
gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi.SelectObject.restype = ctypes.c_void_p
gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi.BitBlt.restype = ctypes.c_int
gdi.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                       ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                       ctypes.c_uint]
gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                          ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_uint]
gdi.DeleteObject.argtypes = [ctypes.c_void_p]
gdi.DeleteDC.argtypes = [ctypes.c_void_p]
u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


def grab(rect):
    x, y, w, h = rect
    hdc = u.GetDC(None)
    mdc = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    try:
        gdi.SelectObject(mdc, bmp)
        gdi.BitBlt(mdc, 0, 0, w, h, hdc, x, y, 0x00CC0020)
        data = ctypes.create_string_buffer(w * h * 4)
        class BI(ctypes.Structure):
            _fields_ = [("s", ctypes.c_uint32), ("w", ctypes.c_int32),
                        ("h", ctypes.c_int32), ("p", ctypes.c_uint16),
                        ("b", ctypes.c_uint16), ("c", ctypes.c_uint32),
                        ("sz", ctypes.c_uint32), ("x", ctypes.c_int32),
                        ("y", ctypes.c_int32), ("cu", ctypes.c_uint32),
                        ("ci", ctypes.c_uint32)]
        bi = BI(ctypes.sizeof(BI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        gdi.GetDIBits(mdc, bmp, 0, h, data, ctypes.byref(bi), 0)
        return ctypes.string_at(data, w * h * 4)
    finally:
        gdi.DeleteObject(bmp)
        gdi.DeleteDC(mdc)
        u.ReleaseDC(None, hdc)


def find_island():
    pids = {p.pid for p in psutil.process_iter(["name"])
            if (p.info["name"] or "") == "DaozaiIsland.exe"}
    hits = []

    def cb(h, _):
        if win32process.GetWindowThreadProcessId(h)[1] in pids:
            r = win32gui.GetWindowRect(h)
            w, hh = r[2] - r[0], r[3] - r[1]
            if w > 200 and 40 < hh < 400:
                hits.append((h, r))
        return True

    win32gui.EnumWindows(cb, None)
    return hits[0] if hits else (None, None)


def click(x, y, wheel=0):
    u.SetCursorPos(x, y)
    time.sleep(0.25)
    if wheel:
        u.mouse_event(0x0800, 0, 0, wheel & 0xFFFFFFFF, 0)
    else:
        u.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.08)
        u.mouse_event(0x0004, 0, 0, 0, 0)


wx = 67852
print("start: visible =", win32gui.IsWindowVisible(wx),
      "fg =", win32gui.GetForegroundWindow())
win32gui.ShowWindow(wx, win32con.SW_HIDE)
time.sleep(0.9)
print("after hide: visible =", win32gui.IsWindowVisible(wx))

isl, ir = find_island()
print("island:", isl, ir)
u.SetCursorPos((ir[0] + ir[2]) // 2, 4)
time.sleep(1.4)
isl, ir = find_island()
print("island after hover:", ir)
icon_x = ir[2] - 134
icon_y = ir[1] + 45
print("click icon at", icon_x, icon_y)
click(icon_x, icon_y)
time.sleep(1.6)
print("after island click: visible =", win32gui.IsWindowVisible(wx),
      "fg match =", win32gui.GetForegroundWindow() == wx,
      "fg =", win32gui.GetForegroundWindow())

r = win32gui.GetWindowRect(wx)
print("wechat rect:", r, "size", r[2] - r[0], r[3] - r[1])
a = grab(r)
click(r[0] + 180, r[1] + 230)
time.sleep(0.8)
b = grab(r)
print("click changed:", a != b)
click(r[0] + 600, r[1] + 350, wheel=-120)
time.sleep(0.8)
c = grab(r)
print("wheel changed:", b != c)
print("final fg:", win32gui.GetWindowText(win32gui.GetForegroundWindow()))
