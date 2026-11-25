"""测量任务栏按钮与托盘图标区域的红色像素数量，用于设定红点阈值。"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uiautomation as auto  # noqa: E402

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.GetDC.restype = ctypes.c_void_p
user32.GetDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                         ctypes.c_uint]
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]


class BIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


def grab(x, y, w, h):
    hdc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    buf = ctypes.create_string_buffer(w * h * 4)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, w, h, hdc, x, y, 0x00CC0020):
            return None
        bi = BIH()
        bi.biSize = ctypes.sizeof(BIH)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0):
            return None
        return buf.raw
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, hdc)


def stats(x, y, w, h):
    data = grab(x, y, w, h)
    if not data:
        return None
    red = strong_red = 0
    g_sum = 0
    n = w * h
    for i in range(0, len(data) - 3, 4):
        b, g, r = data[i], data[i + 1], data[i + 2]
        g_sum += g
        if r > 140 and g < 90 and b < 90:
            red += 1
        if r > 180 and g < 60 and b < 60:
            strong_red += 1
    return red, strong_red, g_sum / n, n


targets = [("微信", "TaskListButton"), ("企业微信", "SystemTray"), ("飞书", "SystemTray")]
root = auto.GetRootControl()
found = []
for _ in range(3):
    found = []

    def walk(ctrl, depth=0):
        if depth > 18:
            return
        try:
            cls = ctrl.ClassName or ""
            name = ctrl.Name or ""
        except Exception:
            return
        if any(name.startswith(t[0]) for t in targets) and any(t[1] in cls for t in targets):
            r = ctrl.BoundingRectangle
            found.append((name, cls, int(r.left), int(r.top), int(r.width()), int(r.height())))
        try:
            for c in ctrl.GetChildren():
                walk(c, depth + 1)
        except Exception:
            return

    walk(root)
    print(f"--- round, found {len(found)} ---")
    for name, cls, l, t, w, h in found:
        s = stats(l, t, w, h)
        print(f"  {name!r:22} cls={cls:28} rect=({l},{t},{w},{h}) "
              f"red={s[0]} strong={s[1]} g={s[2]:.1f} px={s[3]} ratio={s[0]/s[3]*100:.2f}%")
    time.sleep(0.5)
