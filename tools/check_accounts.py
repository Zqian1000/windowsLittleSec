"""按默认配置列出当前识别到的聊天账号，确认企微/飞书是否纳入。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config, im  # noqa: E402

cfg = config.load()
enabled = {
    "wechat": cfg.get("wechat_enabled", True),
    "qq": cfg.get("qq_enabled", True),
    "wecom": cfg.get("wecom_enabled", True),
    "feishu": cfg.get("feishu_enabled", True),
    "dingtalk": cfg.get("dingtalk_enabled", False),
}
print("enabled:", enabled)
print("edge_hide:", cfg.get("edge_hide"))
accounts = im.list_accounts(enabled)
print("accounts:", len(accounts))
for a in accounts:
    print(f"  {a.client_name:6} title={a.title!r} hwnd={a.hwnd} hidden={a.is_hidden}")
