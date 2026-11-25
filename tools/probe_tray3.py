"""在 UIA 树里找托盘图标元素（按名称），并采样其红色像素占比。"""
import ctypes
import os
import sys

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


NAMES = ("微信", "企业微信", "飞书", "QQ", "钉钉")
hits = []
root = auto.GetRootControl()


def walk(ctrl, depth=0):
    if depth > 16:
        return
    try:
        name = ctrl.Name or ""
    except Exception:
        name = ""
    if name and any(name.startswith(n) for n in NAMES):
        hits.append((ctrl, name, depth))
    try:
        for c in ctrl.GetChildren():
            walk(c, depth + 1)
    except Exception:
        return


walk(root)
print("hits:", len(hits))
for ctrl, name, depth in hits:
    try:
        r = ctrl.BoundingRectangle
        w, h = int(r.width()), int(r.height())
        if w <= 0 or h <= 0:
            continue
        data = grab(int(r.left), int(r.top), w, h)
        red = 0
        if data:
            for i in range(0, len(data) - 3, 4):
                b, g, rr = data[i], data[i + 1], data[i + 2]
                if rr > 140 and g < 90 and b < 90:
                    red += 1
        print(f"  name={name!r} cls={ctrl.ClassName!r} type={ctrl.ControlTypeName!r} "
              f"rect=({int(r.left)},{int(r.top)},{w},{h}) red={red} "
              f"({red / (w * h) * 100:.1f}%)")
    except Exception as exc:
        print("  err:", exc)
