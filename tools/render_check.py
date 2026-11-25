"""渲染收起态与展开态各一张图，便于核对布局（不依赖手动点击）。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402
from core import config  # noqa: E402
from core.media import MediaController  # noqa: E402
from island.bar import IslandBar  # noqa: E402


def main() -> int:
    app = QApplication([])
    cfg = config.load()
    # 让所有客户端都显示，便于一次看清多开列表
    for k in ("wechat_enabled", "qq_enabled", "wecom_enabled",
              "feishu_enabled", "dingtalk_enabled"):
        cfg[k] = True
    media = MediaController()
    bar = IslandBar(cfg, media)

    bar.refresh_clock()
    bar._im_signature = None
    bar.refresh_poll()
    app.processEvents()
    bar.grab().save("ui_collapsed.png")
    print("collapsed ->", bar.width(), bar.height(), "accounts:", len(bar._im_accounts))

    bar.toggle()
    bar.refresh_poll()
    app.processEvents()
    bar.grab().save("ui_expanded.png")
    print("expanded ->", bar.width(), bar.height())

    for a in bar._im_accounts:
        print("  ", a.client_name, a.title, "hidden" if a.is_hidden else "")
    media.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
