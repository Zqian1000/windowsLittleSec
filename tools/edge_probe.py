"""探针：每 120ms 打印鼠标位置与岛的状态，配合真实鼠标操作定位问题。"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import config  # noqa: E402
from core.media import MediaController  # noqa: E402
from island.bar import IslandBar  # noqa: E402

user32 = ctypes.windll.user32


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def phys_cursor():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


app = QApplication([])
cfg = config.load()
media = MediaController()
bar = IslandBar(cfg, media)
bar.show()

log = open("probe-log.txt", "w", encoding="utf-8")


def tick():
    q = QCursor.pos()
    log.write(
        f"t={time.monotonic():.1f} phys={phys_cursor()} qt=({q.x()},{q.y()}) "
        f"bar=({bar.x()},{bar.y()},{bar.width()},{bar.height()}) "
        f"expanded={bar.expanded} hidden={bar._auto_hidden} dpr={bar.devicePixelRatioF():.2f}\n"
    )
    log.flush()


timer = QTimer()
timer.timeout.connect(tick)
timer.start(120)


def quit_after():
    media.stop()
    log.close()
    app.quit()


QTimer.singleShot(20000, quit_after)
sys.exit(app.exec())
