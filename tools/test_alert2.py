"""1) 验证账号枚举结果；2) 跨进程验证 EVENT_SYSTEM_ALERT 能否捕获闪动。"""
import ctypes
import time

import win32gui
import win32process
from core import im

print("=== A. 账号枚举 ===")
accs = im.list_accounts({"wechat": True, "qq": True, "wecom": True,
                         "feishu": True, "dingtalk": True})
print("count:", len(accs))
for a in accs:
    print(f"  {a.client_name} pid={a.pid} hwnd={a.hwnd} hidden={a.is_hidden} "
          f"min={a.is_minimized} class={a.class_name} title={a.title!r}")

print("=== B. 跨进程闪动事件 ===")
user32 = ctypes.windll.user32
EVENT_SYSTEM_ALERT = 0x0002
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
hits = []

WINEVENTPROC = ctypes.WINFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
    ctypes.c_long, ctypes.c_long, ctypes.c_uint, ctypes.c_uint,
)


def _cb(hook, event, hwnd, id_object, id_child, tid, ms):
    hits.append((event, hwnd, id_object, id_child))


proc = WINEVENTPROC(_cb)


class FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint), ("uCount", ctypes.c_uint),
                ("dwTimeout", ctypes.c_uint)]


target = next(((a.pid, a.hwnd) for a in accs if a.client_key == "wechat"), None)
print("wechat target:", target)
if target:
    pid, hwnd = target
    hook = user32.SetWinEventHook(EVENT_SYSTEM_ALERT, EVENT_SYSTEM_ALERT,
                                  None, proc, 0, 0, WINEVENT_OUTOFCONTEXT)
    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 3, 5, 0)
    user32.FlashWindowEx(ctypes.byref(info))
    import ctypes.wintypes as wintypes
    msg = wintypes.MSG()
    end = time.time() + 3
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.03)
    user32.UnhookWinEvent(hook)
    # 停闪
    user32.FlashWindowEx(ctypes.byref(FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0, 0, 0)))
    print("hits:", hits)
    print("RESULT:", "CROSS_PROCESS_ALERT_OK" if hits else "NO_CROSS_ALERT")
