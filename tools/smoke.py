"""离屏冒烟测试：验证导入、界面构建、两态切换与数据刷新无异常。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402
from core import config, im, system_info  # noqa: E402
from core.media import MediaController  # noqa: E402
from island.bar import IslandBar  # noqa: E402


def main() -> int:
    app = QApplication([])
    cfg = config.load()
    media = MediaController()
    bar = IslandBar(cfg, media)

    bar.refresh_clock()
    bar.refresh_poll()
    print("collapsed size:", bar.width(), bar.height())

    bar.toggle()
    bar.refresh_poll()
    print("expanded size:", bar.width(), bar.height())

    accounts = im.list_accounts({
        "wechat": True, "qq": True, "wecom": True,
        "feishu": True, "dingtalk": True,
    })
    print("IM accounts:", len(accounts))
    for a in accounts:
        print("  -", a.client_name, "|", a.title, "| hwnd", a.hwnd)

    print("battery:", system_info.battery())
    print("media:", media.get_state())
    media.stop()
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
