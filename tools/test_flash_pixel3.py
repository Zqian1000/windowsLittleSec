"""GDI 按物理像素抓取微信任务栏按钮区域，测闪动前后颜色变化。"""
import ctypes
import time

import uiautomation as auto
import win32gui
import win32process
import psutil

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# 句柄必须按 c_void_p 传递，否则 64 位下会溢出
user32.GetDC.restype = ctypes.c_void_p
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.BitBlt.restype = ctypes.c_int
gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                         ctypes.c_int, ctypes.c_int, ctypes.c_uint]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                            ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_uint]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]


def grab_mean(x, y, w, h):
    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    gdi32.SelectObject(hdc_mem, bmp)
    gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, 0x00CC0020)

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h  # 负数=自上而下
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0

    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf, ctypes.byref(bi), 0)

    tr = tg = tb = 0
    n = w * h
    data = buf.raw
    for i in range(0, n * 4, 4):
        tb += data[i]; tg += data[i + 1]; tr += data[i + 2]

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)
    return (tr / n, tg / n, tb / n) if n else None


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


def find_button():
    found = []

    def walk(ctrl, depth=0):
        if depth > 14 or found:
            return
        try:
            cls = ctrl.ClassName or ""
        except Exception:
            return
        if "TaskListButton" in cls:
            name = ctrl.Name or ""
            if name.startswith("微信") and "企业微信" not in name:
                found.append(ctrl)
                return
        try:
            for c in ctrl.GetChildren():
                walk(c, depth + 1)
        except Exception:
            return

    walk(auto.GetRootControl())
    return found[0] if found else None


hwnd = find_wechat()
btn = find_button()
print("hwnd:", hwnd, "button:", btn.Name if btn else None)
if not btn:
    print("VERDICT: BUTTON_NOT_FOUND")
    raise SystemExit(0)

r = btn.BoundingRectangle
x, y, w, h = int(r.left), int(r.top), int(r.width()), int(r.height())
print("rect:", x, y, w, h)

vals = []


def sample(tag):
    m = grab_mean(x, y, w, h)
    vals.append((tag, m))
    print(f"  {tag}: ({m[0]:.1f},{m[1]:.1f},{m[2]:.1f})")


sample("off1"); sample("off2"); sample("off3")
user32.FlashWindowEx(ctypes.byref(FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 3, 40, 0)))
for i in range(12):
    time.sleep(0.12)
    sample(f"on{i}")
user32.FlashWindowEx(ctypes.byref(FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0, 0, 0)))

on = [m for t, m in vals if t.startswith("on")]
if on:
    spans = [max(m[i] for m in on) - min(m[i] for m in on) for i in range(3)]
    print("on spans R/G/B:", [round(s, 1) for s in spans])
    print("VERDICT:", "PIXEL_CHANGES" if max(spans) > 4 else "PIXEL_STABLE")
