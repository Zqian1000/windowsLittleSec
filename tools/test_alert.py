"""验证 EVENT_SYSTEM_ALERT 是否能捕获 FlashWindowEx 的闪动请求。

若能捕获，未读红点就有可靠信号；若不能，需要换判定方式。
"""
import ctypes
import time

user32 = ctypes.windll.user32

EVENT_SYSTEM_ALERT = 0x0002
WINEVENT_OUTOFCONTEXT = 0x0000

hits = []

WINEVENTPROC = ctypes.WINFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
    ctypes.c_long, ctypes.c_long, ctypes.c_uint, ctypes.c_uint,
)


def _cb(hook, event, hwnd, id_object, id_child, tid, ms):
    hits.append((event, hwnd))


proc = WINEVENTPROC(_cb)

FLASHW_ALL = 3
FLASHW_TIMERNOFG = 12


class FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hwnd", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint),
        ("uCount", ctypes.c_uint),
        ("dwTimeout", ctypes.c_uint),
    ]


def main() -> None:
    hwnd = user32.CreateWindowExW(
        0, "STATIC", "flash-test", 0x00CF0000,
        100, 100, 200, 100, None, None, None, None
    )
    user32.ShowWindow(hwnd, 5)

    hook = user32.SetWinEventHook(
        EVENT_SYSTEM_ALERT, EVENT_SYSTEM_ALERT, None, proc, 0, 0,
        WINEVENT_OUTOFCONTEXT
    )
    print("hook handle:", hook)

    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd,
                      FLASHW_ALL | FLASHW_TIMERNOFG, 3, 0)
    ok = user32.FlashWindowEx(ctypes.byref(info))
    print("FlashWindowEx ok:", ok)

    # 抽消息，让 OUTOFCONTEXT 回调得以派发
    msg = ctypes.wintypes.MSG() if hasattr(ctypes, "wintypes") else None
    import ctypes.wintypes as wintypes
    msg = wintypes.MSG()
    end = time.time() + 3
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.05)

    user32.UnhookWinEvent(hook)
    print("hits:", hits)
    print("RESULT:", "ALERT_FIRED" if hits else "NO_ALERT")


if __name__ == "__main__":
    main()
