"""验证：任务栏按钮闪动时，像素是否出现可检测的交替。

用自己创建的窗口做对照（同进程也能闪动），观察按钮区域平均色变化。
"""
import sys

import uiautomation as auto
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QWidget

TITLE = "FlashProbeWindow"
samples = []


def find_button(verbose=False):
    found = []
    all_names = []

    def walk(ctrl, depth=0):
        if depth > 14 or found:
            return
        try:
            cls = ctrl.ClassName or ""
        except Exception:
            return
        if "TaskListButton" in cls:
            try:
                name = ctrl.Name or ""
                all_names.append(name)
                if TITLE in name:
                    found.append(ctrl)
                    return
            except Exception:
                pass
        try:
            for c in ctrl.GetChildren():
                walk(c, depth + 1)
        except Exception:
            return

    walk(auto.GetRootControl())
    if verbose and not found:
        print("button names:", all_names[:12])
    return found[0] if found else None


def grab_mean(rect):
    img: QImage = QGuiApplication.primaryScreen().grabWindow(
        0, int(rect.left), int(rect.top),
        max(1, int(rect.width())), max(1, int(rect.height()))
    )
    if img.isNull():
        return None
    total = [0, 0, 0]
    count = 0
    for y in range(0, img.height(), 3):
        for x in range(0, img.width(), 3):
            c = img.pixelColor(x, y)
            total[0] += c.red()
            total[1] += c.green()
            total[2] += c.blue()
            count += 1
    return tuple(v / count for v in total) if count else None


def main() -> int:
    app = QApplication(sys.argv)
    w = QWidget()
    w.setWindowTitle(TITLE)
    w.resize(400, 200)
    w.show()
    hwnd = int(w.winId())

    def start():
        btn = find_button(verbose=True)
        if btn is None:
            print("BUTTON_NOT_FOUND")
            app.quit()
            return
        rect = btn.BoundingRectangle
        print("button rect:", rect)

        def sample(tag):
            m = grab_mean(rect)
            samples.append((tag, m))
            print(f"  {tag}: {m}")

        sample("base1")
        sample("base2")

        import ctypes
        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p),
                        ("dwFlags", ctypes.c_uint), ("uCount", ctypes.c_uint),
                        ("dwTimeout", ctypes.c_uint)]
        user32 = ctypes.windll.user32
        user32.FlashWindowEx(ctypes.byref(
            FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 3, 20, 0)))

        def tick(n=[0]):
            n[0] += 1
            sample(f"flash{n[0]}")
            if n[0] < 8:
                QTimer.singleShot(120, tick)
            else:
                user32.FlashWindowEx(ctypes.byref(
                    FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0, 0, 0)))
                QTimer.singleShot(300, app.quit)

        QTimer.singleShot(120, tick)

    QTimer.singleShot(4000, start)
    app.exec()

    base = [m for t, m in samples if m]
    if len(base) >= 3:
        flash_vals = [m for t, m in samples if t.startswith("flash") and m]
        if flash_vals:
            rng = max(v[0] for v in flash_vals) - min(v[0] for v in flash_vals)
            print(f"RESULT: red-range={rng:.1f}")
            print("VERDICT:", "PIXEL_CHANGES" if rng > 4 else "PIXEL_STABLE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
