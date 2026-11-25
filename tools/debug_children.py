"""列出收起态页面下的所有子控件及其几何位置，定位多余控件。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402
from core import config  # noqa: E402
from core.media import MediaController  # noqa: E402
from island.bar import IslandBar  # noqa: E402

app = QApplication([])
cfg = config.load()
for k in ("wechat_enabled", "qq_enabled", "wecom_enabled",
          "feishu_enabled", "dingtalk_enabled"):
    cfg[k] = True
media = MediaController()
bar = IslandBar(cfg, media)
bar.refresh_clock()
bar._im_signature = None
bar.refresh_poll()
app.processEvents()

print("bar size:", bar.width(), bar.height())
print("collapsed page children:")
from PySide6.QtWidgets import QWidget  # noqa: E402

for w in bar.collapsed.findChildren(QWidget):
    cls = w.metaObject().className()
    try:
        txt = w.text()
    except Exception:
        txt = getattr(w, "client_key", "")
    g = w.geometry()
    print(f"  {cls:22} {txt!r:12} geom=({g.x()},{g.y()},{g.width()},{g.height()}) "
          f"visible={w.isVisible()}")
media.stop()
