import sys, time
sys.path.insert(0, r"C:\Users\41587\Desktop\1\daozai")
from core.unread import FlashWatcher
from core import im
w = FlashWatcher()
accounts = im.list_accounts({"wechat":True,"qq":True,"wecom":True,"feishu":True,"dingtalk":True})
print("accounts:", [(a.client_key, a.hwnd, a.title, a.is_foreground) for a in accounts])
fg = {a.client_key: True for a in accounts if a.is_foreground}
for i in range(4):
    st = w.poll({"wechat":True,"qq":True,"wecom":True,"feishu":True,"dingtalk":True}, fg)
    print(i, st, "taskbar:", w._taskbar)
    time.sleep(0.5)
