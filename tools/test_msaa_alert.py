"""验证 MSAA 的 STATE_SYSTEM_ALERT 能否反映窗口闪动状态（跨进程）。

若闪动期间 accState 出现 ALERT_HIGH/LOW 位，未读红点即可靠实现。
"""
import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.windll.user32
oleacc = ctypes.windll.oleacc

OBJID_WINDOW = 0
STATE_SYSTEM_ALERT_HIGH = 0x00100000
STATE_SYSTEM_ALERT_LOW = 0x00400000
VT_I4 = 3


class VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort),
                ("wReserved1", ctypes.c_ushort),
                ("wReserved2", ctypes.c_ushort),
                ("wReserved3", ctypes.c_ushort),
                ("lVal", ctypes.c_long)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


IID_IAccessible = GUID(
    0x618736E0, 0x3C3D, 0x11CF,
    (ctypes.c_ubyte * 8)(0x81, 0x0C, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71)
)


def acc_state(hwnd: int):
    pacc = ctypes.c_void_p()
    hr = oleacc.AccessibleObjectFromWindow(
        wt.HWND(hwnd), ctypes.c_ulong(OBJID_WINDOW),
        ctypes.byref(IID_IAccessible), ctypes.byref(pacc))
    if hr != 0 or not pacc:
        return None, hr

    vtbl = ctypes.cast(pacc, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, VARIANT,
                            ctypes.POINTER(VARIANT))(vtbl[14])

    child = VARIANT()
    child.vt = VT_I4
    child.lVal = 0
    out = VARIANT()
    out.vt = 0
    fn(pacc, child, ctypes.byref(out))
    state = out.lVal

    # Release
    vtbl_rel = ctypes.cast(pacc, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl_rel[2])(pacc)
    return state, hr


class FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint), ("uCount", ctypes.c_uint),
                ("dwTimeout", ctypes.c_uint)]


def flash(hwnd, on):
    user32.FlashWindowEx(ctypes.byref(FLASHWINFO(
        ctypes.sizeof(FLASHWINFO), hwnd, 3 if on else 0, 5, 0)))


def find_wechat():
    import win32gui
    import win32process
    import psutil
    found = []

    def enum(hwnd, _):
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        if title != "微信":
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name().lower()
        except Exception:
            return
        if name == "weixin.exe" and cls.startswith("Qt"):
            found.append(hwnd)

    win32gui.EnumWindows(enum, None)
    return found[0] if found else 0


hwnd = find_wechat()
print("wechat hwnd:", hwnd)
if hwnd:
    s0, hr0 = acc_state(hwnd)
    print(f"before flash: state={s0} hr={hr0}")
    flash(hwnd, True)
    for i in range(6):
        time.sleep(0.25)
        s, hr = acc_state(hwnd)
        flags = []
        if s is not None:
            if s & STATE_SYSTEM_ALERT_HIGH:
                flags.append("ALERT_HIGH")
            if s & STATE_SYSTEM_ALERT_LOW:
                flags.append("ALERT_LOW")
        print(f"  flash[{i}] state={s} hr={hr} flags={flags or 'none'}")
    flash(hwnd, False)
    time.sleep(0.3)
    s1, _ = acc_state(hwnd)
    print(f"after stop: state={s1}")
    print("RESULT:", "ALERT_FLAG_SEEN"
          if any("ALERT" in str(f) for f in []) else "see above")
