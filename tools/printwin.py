"""用 PrintWindow(PW_RENDERFULLCONTENT) 抓窗口，绕过 DComp 的屏幕 DC 假象。"""
import ctypes, sys, os, time
from ctypes import wintypes
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import win32gui

u = ctypes.windll.user32
gdi = ctypes.windll.gdi32
u.SetProcessDPIAware()
u.GetDC.restype = ctypes.c_void_p
u.GetDC.argtypes = [ctypes.c_void_p]
u.ReleaseDC.restype = ctypes.c_int
u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
gdi.CreateCompatibleDC.restype = ctypes.c_void_p
gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi.SelectObject.restype = ctypes.c_void_p
gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi.GetDIBits.restype = ctypes.c_int
gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
gdi.DeleteObject.argtypes = [ctypes.c_void_p]
gdi.DeleteDC.argtypes = [ctypes.c_void_p]


class BI(ctypes.Structure):
    _fields_ = [("s", ctypes.c_uint32), ("w", ctypes.c_int32), ("h", ctypes.c_int32),
                ("p", ctypes.c_uint16), ("b", ctypes.c_uint16), ("c", ctypes.c_uint32),
                ("sz", ctypes.c_uint32), ("x", ctypes.c_int32), ("y", ctypes.c_int32),
                ("cu", ctypes.c_uint32), ("ci", ctypes.c_uint32)]


def pw(hwnd):
    r = win32gui.GetWindowRect(hwnd)
    w, h = r[2] - r[0], r[3] - r[1]
    sdc = u.GetDC(None)
    mdc = gdi.CreateCompatibleDC(sdc)
    bmp = gdi.CreateCompatibleBitmap(sdc, w, h)
    buf = ctypes.create_string_buffer(w * h * 4)
    try:
        gdi.SelectObject(mdc, bmp)
        ok = u.PrintWindow(hwnd, mdc, 2)
        bi = BI(ctypes.sizeof(BI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        gdi.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        data, okv = buf.raw, ok
    finally:
        gdi.DeleteObject(bmp)
        gdi.DeleteDC(mdc)
        u.ReleaseDC(None, sdc)
    return data, okv, w, h


def save(data, w, h, path):
    import struct
    body = bytearray()
    stride = w * 4
    for j in range(h - 1, -1, -1):
        for i in range(0, stride, 4):
            px = data[j * stride + i:j * stride + i + 3]
            body += bytes((px[2], px[1], px[0], 0))
    fh = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(body), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, len(body), 0, 0, 0, 0)
    open(path, "wb").write(fh + info + bytes(body))


if __name__ == "__main__":
    hw = int(sys.argv[1])
    data, ok, w, h = pw(hw)
    save(data, w, h, sys.argv[2])
    nonblack = sum(1 for i in range(0, len(data), 4) if data[i] or data[i + 1] or data[i + 2])
    print(f"hwnd={hw} ok={ok} size={w}x{h} nonblack={nonblack}")
