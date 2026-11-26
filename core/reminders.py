"""提醒的存储：内容、时间、可选截图附件，全部留在本机。

只有数据和读写，不碰界面；到点该不该弹由 `island/reminders_ui.py` 判断。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core import config
from core import recurrence

# 任务标签：普通 / 紧急 / 重要 / 可延后。保留 important 作为旧数据兼容。
PRIORITY_KEYS = ("normal", "urgent", "important", "later")
PRIORITY_LABELS = {
    "normal": "普通",
    "urgent": "紧急",
    "important": "重要",
    "later": "可延后",
}
PRIORITY_ORDER = PRIORITY_KEYS
PRIORITY_COLORS = {
    "normal": "#8e8e93",
    "urgent": "#ff453a",
    "important": "#ffd60a",
    "later": "#64d2ff",
}


def normalize_priority(value) -> str:
    value = str(value or "normal").strip().lower()
    return value if value in PRIORITY_KEYS else "normal"


@dataclass
class Reminder:
    id: str
    text: str
    due: float                 # epoch 秒
    created: float = 0.0
    image: str = ""            # 截图附件的本地路径
    done: bool = False
    notified: bool = False     # 已经弹过内容泡，避免重复弹
    acked: bool = False        # 内容泡已被关掉（看过但还没做），不再挂角标
    priority: str = "normal"   # normal / urgent / important / later
    recurrence: str = recurrence.NONE   # none / daily / weekdays / weekly / monthly
    base_due: float = 0.0      # 周期计划锚点：改期才更新，snooze 不动它

    @property
    def important(self) -> bool:
        """旧代码/旧 JSON 用的布尔字段，等价于 priority == important。"""
        return self.priority == "important"

    @important.setter
    def important(self, value: bool) -> None:
        if value:
            self.priority = "important"
        elif self.priority == "important":
            self.priority = "normal"

    @property
    def overdue(self) -> bool:
        return not self.done and not self.acked and self.due <= time.time()

    def due_text(self) -> str:
        t = time.localtime(self.due)
        today = time.localtime()
        if (t.tm_year, t.tm_yday) == (today.tm_year, today.tm_yday):
            raw = time.strftime("%H:%M", t)
        else:
            raw = time.strftime("%m-%d %H:%M", t)
        rep = self.repeat_text()
        return f"{rep} · {raw}" if rep else raw

    def repeat_text(self) -> str:
        return "" if self.recurrence == recurrence.NONE else recurrence.label(self.recurrence)

    @property
    def priority_label(self) -> str:
        return PRIORITY_LABELS.get(self.priority, PRIORITY_LABELS["normal"])

    @property
    def is_important(self) -> bool:
        return self.priority == "important"

    def schedule_text(self) -> str:
        """列表里显示的时间：周期任务直接带「每天 / 工作日」前缀。"""
        return self.due_text()

    def to_json(self) -> dict:
        return {"id": self.id, "text": self.text, "due": self.due,
                "created": self.created, "image": self.image,
                "done": self.done, "notified": self.notified,
                "acked": self.acked, "recurrence": self.recurrence,
                "base_due": self.base_due, "priority": self.priority}


class ReminderStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or (config.config_dir() / "reminders.json")
        self._items: list[Reminder] = []
        self.load()

    # ---- 磁盘 ----
    def load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._items = []
            return
        items: list[Reminder] = []
        for row in raw.get("items", []) if isinstance(raw, dict) else []:
            try:
                items.append(Reminder(
                    id=str(row["id"]),
                    text=str(row.get("text", "")),
                    due=float(row["due"]),
                    created=float(row.get("created", 0)),
                    image=str(row.get("image", "")),
                    done=bool(row.get("done", False)),
                    notified=bool(row.get("notified", False)),
                    acked=bool(row.get("acked", False)),
                    priority=normalize_priority(
                        row.get("priority", "important" if row.get("important") else "normal")),
                    recurrence=recurrence.normalize(row.get("recurrence", "")),
                    base_due=float(row.get("base_due", row.get("due", 0)) or 0),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        self._items = items
        self._prune()

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"items": [i.to_json() for i in self._items]},
                           ensure_ascii=False),
                encoding="utf-8")
        except OSError:
            pass

    def _prune(self) -> None:
        """已完成的留 7 天，太老的自动清掉，别让列表越滚越长。"""
        cutoff = time.time() - 7 * 86400
        self._items = [i for i in self._items if not (i.done and i.due < cutoff)]

    # ---- 读 ----
    def items(self) -> list[Reminder]:
        """未完成按时间升序在前，已完成的按时间倒序垫后。"""
        pending = sorted((i for i in self._items if not i.done), key=lambda i: i.due)
        done = sorted((i for i in self._items if i.done), key=lambda i: -i.due)
        return pending + done

    def pending(self) -> list[Reminder]:
        return [i for i in self.items() if not i.done]

    def get(self, rid: str) -> Optional[Reminder]:
        return next((i for i in self._items if i.id == rid), None)

    def due_now(self, now: Optional[float] = None) -> list[Reminder]:
        now = time.time() if now is None else now
        return [i for i in self._items
                if not i.done and not i.notified and i.due <= now]

    def overdue_count(self) -> int:
        """到点还没完成的条数，给胶囊上的铃铛做角标。"""
        return sum(1 for i in self._items if i.overdue)

    # ---- 写 ----
    def add(self, text: str, due: float, image: str = "",
            priority: str = "normal",
            recurrence_rule: str = recurrence.NONE,
            important: Optional[bool] = None) -> Reminder:
        # 兼容两种老调用顺序：add(text, due, priority) / add(text, due, image, rule)
        if image in PRIORITY_KEYS and priority == "normal":
            image, priority = "", image
        if priority in recurrence.RULES and recurrence_rule == recurrence.NONE:
            recurrence_rule, priority = priority, "normal"
        if important is not None:
            priority = "important" if important else "normal"
        rule = recurrence.normalize(recurrence_rule)
        item = Reminder(id=uuid.uuid4().hex[:10], text=text.strip(),
                        due=float(due), created=time.time(), image=image,
                        priority=normalize_priority(priority),
                        recurrence=rule, base_due=float(due))
        self._items.append(item)
        self.save()
        return item

    def remove(self, rid: str) -> None:
        item = self.get(rid)
        if item is not None and item.image:
            # 只删我们自己复制进配置目录的那份；指向别处的图（复制失败时）
            # 原文件不能动
            try:
                p = Path(item.image)
                if (config.config_dir() / "reminders") in p.parents:
                    p.unlink(missing_ok=True)
            except OSError:
                pass
        self._items = [i for i in self._items if i.id != rid]
        self.save()

    def update(self, rid: str, **fields) -> Reminder | None:
        """局部更新一条提醒；界面只传要改的字段。"""
        item = self.get(rid)
        if item is None:
            return None
        if "text" in fields:
            item.text = str(fields["text"] or "").strip()
        if "due" in fields:
            item.due = float(fields["due"])
            item.base_due = item.due
            item.notified = False
            item.acked = False
            item.done = False
        if "image" in fields:
            item.image = str(fields["image"] or "")
        if "priority" in fields:
            item.priority = normalize_priority(fields["priority"])
        if "important" in fields:
            item.priority = "important" if fields["important"] else "normal"
        if "recurrence" in fields:
            item.recurrence = recurrence.normalize(fields["recurrence"])
        if "recurrence_rule" in fields:
            item.recurrence = recurrence.normalize(fields["recurrence_rule"])
        self.save()
        return item

    def set_done(self, rid: str, done: bool) -> None:
        """抽屉勾选框：完成周期任务要滚到下一次，不是简单置 done。"""
        item = self.get(rid)
        if item is None:
            return
        if done:
            self.mark_done(rid, True)
            return
        item.done = False
        item.notified = item.due <= time.time()
        item.acked = False
        self.save()

    def set_priority(self, rid: str, priority: str) -> bool:
        item = self.get(rid)
        if item is None:
            return False
        item.priority = normalize_priority(priority)
        self.save()
        return True

    def set_important(self, rid: str, important: bool) -> bool:
        """旧接口：true 升为重要，false 只把「重要」降回普通。"""
        item = self.get(rid)
        if item is None:
            return False
        if important:
            item.priority = "important"
        elif item.priority == "important":
            item.priority = "normal"
        self.save()
        return True

    def mark_done(self, rid: str, done: bool = True) -> Reminder | None:
        """完成一条；周期任务自动滚到下一次，回到未完成状态。"""
        item = self.get(rid)
        if item is None:
            return None
        if done and item.recurrence != recurrence.NONE:
            # 注意不要改写 base_due：它是原始计划日（如 1/31）。每轮都改的话，
            # 2 月落到 28 号后就再也回不到 31 号了。
            anchor = item.base_due or item.due
            # 提前点完成（比如 18:00 的每日任务上午就做完了）不能把当天 18:00
            # 又当成"下一次"，从当前计划点之后一天起算。
            nxt = recurrence.next_due(anchor, item.recurrence,
                                      now=max(time.time(), float(item.due) + 1.0))
            if nxt is not None:
                item.due = nxt
                item.done = False
                item.notified = False
                item.acked = False
                self.save()
                return item
        item.done = done
        item.notified = item.notified or done
        item.acked = item.acked or done
        self.save()
        return item

    def mark_notified(self, rid: str) -> None:
        item = self.get(rid)
        if item is None:
            return
        item.notified = True
        self.save()

    def mark_acked(self, rid: str) -> None:
        """内容泡点了 ✕：看过就算了，条目留着但不再挂角标。

        周期任务点 ✕ 等于"这次不做了"，直接滚到下一次，别把周期卡死。
        """
        item = self.get(rid)
        if item is None:
            return
        if item.recurrence != recurrence.NONE:
            anchor = item.base_due or item.due
            nxt = recurrence.next_due(anchor, item.recurrence,
                                      now=max(time.time(), float(item.due) + 1.0))
            if nxt is not None:
                item.due = nxt
                item.notified = False
                item.acked = False
                self.save()
                return
        item.acked = True
        item.notified = True
        self.save()

    def snooze(self, rid: str, seconds: int = 300) -> None:
        item = self.get(rid)
        if item is None:
            return
        item.due = time.time() + seconds
        item.notified = False
        item.acked = False
        self.save()

    def reschedule(self, rid: str, due: float) -> None:
        item = self.get(rid)
        if item is None:
            return
        item.due = float(due)
        item.base_due = float(due)     # 手动改期后，周期从新时间重新算
        item.notified = False
        item.acked = False
        item.done = False
        self.save()
