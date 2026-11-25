"""真实桌面验证：启动本体 → 截收起态 → 模拟点击展开 → 截展开态 → 退出。

用 Qt 自身的 QScreen.grabWindow(0) 抓全屏，避免 DPI 换算问题。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import config  # noqa: E402
from core.media import MediaController  # noqa: E402
from island.bar import IslandBar  # noqa: E402

TIMERS = []


def later(ms, fn):
    t = QTimer()
    t.setSingleShot(True)
    t.timeout.connect(fn)
    t.start(ms)
    TIMERS.append(t)


def main() -> int:
    app = QApplication([])
    cfg = config.load()
    media = MediaController()
    bar = IslandBar(cfg, media)
    bar.show()
    bar.refresh_clock()
    bar.refresh_poll()
    screen = QGuiApplication.primaryScreen()

    def grab(name):
        shot = screen.grabWindow(0)
        ok = shot.save(name)
        print(f"[grab] {name} {shot.width()}x{shot.height()} saved={ok}", flush=True)

    def step1():
        print(f"[1] collapsed {bar.width()}x{bar.height()} at ({bar.x()},{bar.y()})", flush=True)
        grab("live_collapsed.png")
        later(1200, step2)

    def step2():
        QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier,
                         QPoint(bar.width() // 2, bar.height() // 2))
        print(f"[2] expanded={bar.expanded} {bar.width()}x{bar.height()}", flush=True)
        # 等窗口重排与重绘真正上屏后再截屏
        later(600, step2b)

    def step2b():
        grab("live_expanded.png")
        later(600, step3)

    def step3():
        QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, QPoint(bar.width() // 2, 12))
        print(f"[3] expanded={bar.expanded}", flush=True)
        media.stop()
        app.quit()

    later(1200, step1)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
