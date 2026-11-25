"""列出所有任务栏按钮，确认是否存在多个同名按钮（多显示器各一个）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uiautomation as auto  # noqa: E402

rows = []
root = auto.GetRootControl()


def walk(ctrl, depth=0):
    if depth > 18:
        return
    try:
        cls = ctrl.ClassName or ""
    except Exception:
        return
    if "TaskListButton" in cls:
        try:
            name = ctrl.Name or ""
        except Exception:
            name = ""
        r = ctrl.BoundingRectangle
        rows.append((name, int(r.left), int(r.top), int(r.width()), int(r.height())))
    try:
        for c in ctrl.GetChildren():
            walk(c, depth + 1)
    except Exception:
        return


walk(root)
print("buttons:", len(rows))
for name, l, t, w, h in rows:
    print(f"  {name!r:34} rect=({l},{t},{w},{h})")
