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

    @property
    def overdue(self) -> bool:
        return not self.done and not self.acked and self.due <= time.time()

    def due_text(self) -> str:
        t = time.localtime(self.due)
        today = time.localtime()
        if (t.tm_year, t.tm_yday) == (today.tm_year, today.tm_yday):
            return time.strftime("%H:%M", t)
        return time.strftime("%m-%d %H:%M", t)

    def to_json(self) -> dict:
        return {"id": self.id, "text": self.text, "due": self.due,
                "created": self.created, "image": self.image,
                "done": self.done, "notified": self.notified,
                "acked": self.acked}


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
    def add(self, text: str, due: float, image: str = "") -> Reminder:
        item = Reminder(id=uuid.uuid4().hex[:10], text=text.strip(),
                        due=float(due), created=time.time(), image=image)
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

    def mark_done(self, rid: str, done: bool = True) -> None:
        item = self.get(rid)
        if item is None:
            return
        item.done = done
        item.notified = item.notified or done
        item.acked = item.acked or done
        self.save()

    def mark_notified(self, rid: str) -> None:
        item = self.get(rid)
        if item is None:
            return
        item.notified = True
        self.save()

    def mark_acked(self, rid: str) -> None:
        """内容泡点了 ✕：看过就算了，条目留着但不再挂角标。"""
        item = self.get(rid)
        if item is None:
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
        item.notified = False
        item.acked = False
        item.done = False
        self.save()
