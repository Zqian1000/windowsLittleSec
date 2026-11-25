"""对照实验：任务栏按钮在「闪动 / 不闪动」两种状态下，UIA 属性是否有差异。

有差异 => 未读红点可用 UIA 实现；无差异 => 红点需另换判定方式。
"""
import ctypes
import sys

import uiautomation as auto
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget

user32 = ctypes.windll.user32
TITLE = "FlashProbeWindow"


class FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hwnd", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint),
        ("uCount", ctypes.c_uint),
        ("dwTimeout", ctypes.c_uint),
    ]


def flash(hwnd: int, on: bool) -> None:
    flags = 3 if on else 0  # FLASHW_ALL / FLASHW_STOP
    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, flags, 5, 0)
    user32.FlashWindowEx(ctypes.byref(info))


def dump(label: str) -> None:
    candidates = []
    seen = {"n": 0}

    def walk(ctrl, depth=0):
        if depth > 14 or seen["n"] > 4000:
            return
        seen["n"] += 1
        try:
            cls = ctrl.ClassName or ""
        except Exception:
            return
        if "TaskListButton" in cls:
            candidates.append(ctrl)
            return
        try:
            for c in ctrl.GetChildren():
                walk(c, depth + 1)
        except Exception:
            return

    try:
        walk(auto.GetRootControl())
    except Exception as e:
        print("walk error:", e)

    print(f"--- {label} | taskbar buttons={len(candidates)} ---")
    for c in candidates:
        try:
            name = c.Name
            status = c.ItemStatus
            print(f"  name={name!r} itemStatus={status!r} class={c.ClassName}")
        except Exception as e:
            print("  <err>", e)


def main() -> int:
    app = QApplication(sys.argv)
    w = QWidget()
    w.setWindowTitle(TITLE)
    w.resize(400, 200)
    w.show()

    hwnd = int(w.winId())

    def step1():
        # 让另一个窗口拿到前台，本窗口才会真正进入闪动状态
        print("hwnd:", hwnd)
        dump("NO_FLASH")
        flash(hwnd, True)
        QTimer.singleShot(800, step2)

    def step2():
        dump("FLASHING")
        flash(hwnd, False)
        QTimer.singleShot(400, app.quit)

    QTimer.singleShot(1200, step1)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
