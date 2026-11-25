"""用 GDI 按物理像素抓窗口，存 BMP（避免 PowerShell 截屏的坐标偏移）。"""
import ctypes, sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

u = ctypes.windll.user32
gdi = ctypes.windll.gdi32
u.SetProcessDPIAware()
u.GetDC.restype = ctypes.c_void_p
u.GetDC.argtypes = [ctypes.c_void_p]
u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi.CreateCompatibleDC.restype = ctypes.c_void_p
gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi.SelectObject.restype = ctypes.c_void_p
gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi.BitBlt.restype = ctypes.c_int
gdi.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                       ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
gdi.GetDIBits.restype = ctypes.c_int
gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
gdi.DeleteObject.argtypes = [ctypes.c_void_p]
gdi.DeleteDC.argtypes = [ctypes.c_void_p]


def grab(rect, path):
    x, y, w, h = rect
    hdc = u.GetDC(None)
    mdc = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    buf = ctypes.create_string_buffer(w * h * 4)

    class BI(ctypes.Structure):
        _fields_ = [("s", ctypes.c_uint32), ("w", ctypes.c_int32), ("h", ctypes.c_int32),
                    ("p", ctypes.c_uint16), ("b", ctypes.c_uint16), ("c", ctypes.c_uint32),
                    ("sz", ctypes.c_uint32), ("x", ctypes.c_int32), ("y", ctypes.c_int32),
                    ("cu", ctypes.c_uint32), ("ci", ctypes.c_uint32)]

    try:
        gdi.SelectObject(mdc, bmp)
        gdi.BitBlt(mdc, 0, 0, w, h, hdc, x, y, 0x00CC0020)
        bi = BI(ctypes.sizeof(BI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        gdi.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        data = buf.raw
    finally:
        gdi.DeleteObject(bmp)
        gdi.DeleteDC(mdc)
        u.ReleaseDC(None, hdc)

    rows = []
    stride = w * 4
    for j in range(h - 1, -1, -1):
        row = data[j * stride:(j + 1) * stride]
        flipped = bytearray()
        for i in range(0, len(row), 4):
            flipped += bytes((row[i + 2], row[i + 1], row[i], 0))
        rows.append(bytes(flipped))
    body = b"".join(rows)
    file_header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(body), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, len(body), 0, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(file_header + info + body)
    return data


if __name__ == "__main__":
    import win32gui
    hwnd = int(sys.argv[1])
    out = sys.argv[2]
    r = win32gui.GetWindowRect(hwnd)
    grab((r[0], r[1], r[2] - r[0], r[3] - r[1]), out)
    print("saved", out, r)
