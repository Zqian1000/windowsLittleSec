"""打印各 IM 进程的顶层窗口（类名/标题/可见/最小化/尺寸），用于校正识别规则。"""
import win32gui
import win32process
import psutil

TARGETS = {"weixin.exe", "wechat.exe", "qq.exe", "wxwork.exe",
           "feishu.exe", "dingtalk.exe"}

pid_names = {}
for p in psutil.process_iter(["pid", "name"]):
    try:
        if (p.info["name"] or "").lower() in TARGETS:
            pid_names[p.info["pid"]] = p.info["name"]
    except Exception:
        pass

print("target processes:", pid_names)

rows = []

def enum(hwnd, _):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return
    if pid not in pid_names:
        return
    cls = win32gui.GetClassName(hwnd)
    title = win32gui.GetWindowText(hwnd)
    vis = bool(win32gui.IsWindowVisible(hwnd))
    ico = bool(win32gui.IsIconic(hwnd))
    rect = win32gui.GetWindowRect(hwnd)
    rows.append((pid_names[pid], pid, hwnd, cls, title, vis, ico,
                 (rect[2] - rect[0], rect[3] - rect[1])))

win32gui.EnumWindows(enum, None)

print(f"total windows: {len(rows)}")
for r in sorted(rows, key=lambda x: (x[0], x[3])):
    exe, pid, hwnd, cls, title, vis, ico, size = r
    print(f"{exe:12} pid={pid:<6} hwnd={hwnd:<9} class={cls:<28} "
          f"vis={vis!s:<5} min={ico!s:<5} size={size} title={title!r}")
