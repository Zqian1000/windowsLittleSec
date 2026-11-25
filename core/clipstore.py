"""剪贴板历史：只存文字，固定项不会被挤掉，落盘到配置目录。

界面和后台都不碰图片、文件，也不读剪贴板里的富文本格式；写入前由
`island/clipboard.py` 判断这段内容是否被密码管理器标记成"禁止采集"。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from core import config

# 单条上限，超长文本（日志、代码整段）截断保存，避免历史文件失控
MAX_TEXT = 20_000


@dataclass
class ClipItem:
    text: str
    ts: float
    pinned: bool = False

    @property
    def key(self) -> str:
        return hashlib.md5(self.text.encode("utf-8", "ignore")).hexdigest()[:16]

    def preview(self, lines: int = 2, width: int = 46) -> str:
        """压成两三行给界面用，换行和连续空白都折叠掉。"""
        flat = " ".join(self.text.split())
        if len(flat) > width * lines:
            flat = flat[: width * lines - 1] + "…"
        return flat

    def clock(self) -> str:
        t = time.localtime(self.ts)
        today = time.localtime()
        if (t.tm_year, t.tm_yday) == (today.tm_year, today.tm_yday):
            return time.strftime("%H:%M", t)
        return time.strftime("%m-%d %H:%M", t)

    def to_json(self) -> dict:
        return {"text": self.text, "ts": self.ts, "pinned": self.pinned}


class ClipStore:
    """最近 N 条文字剪贴板记录；固定的条目单独保留，不占 N 的名额。"""

    def __init__(self, limit: int = 30, path: Path | None = None) -> None:
        self.limit = max(5, int(limit))
        self._path = path or (config.config_dir() / "clipboard.json")
        self._items: list[ClipItem] = []
        self.load()

    # ---- 磁盘 ----
    def load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._items = []
            return
        items: list[ClipItem] = []
        for row in raw.get("items", []) if isinstance(raw, dict) else []:
            try:
                text = str(row.get("text", ""))
                if not text:
                    continue
                items.append(ClipItem(text=text[:MAX_TEXT],
                                      ts=float(row.get("ts", 0)),
                                      pinned=bool(row.get("pinned", False))))
            except (TypeError, ValueError):
                continue
        self._items = items
        self._trim()

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"items": [i.to_json() for i in self._items]},
                           ensure_ascii=False),
                encoding="utf-8")
        except OSError:
            pass

    # ---- 读 ----
    def items(self) -> list[ClipItem]:
        """固定的在前，各自按时间倒序；界面直接照这个顺序排。"""
        return sorted(self._items, key=lambda i: (not i.pinned, -i.ts))

    def count(self) -> int:
        return len(self._items)

    def get(self, key: str) -> ClipItem | None:
        return next((i for i in self._items if i.key == key), None)

    # ---- 写 ----
    def add(self, text: str, pinned: bool = False) -> ClipItem | None:
        """同一段文字不重复存，只把时间和固定的状态更新一下。"""
        text = (text or "")[:MAX_TEXT]
        if not text.strip():
            return None
        item = ClipItem(text=text, ts=time.time(), pinned=pinned)
        old = self.get(item.key)
        if old is not None:
            old.ts = item.ts
            old.pinned = old.pinned or pinned
            self.save()
            return old
        self._items.append(item)
        self._trim()
        self.save()
        return item

    def toggle_pin(self, key: str) -> bool:
        item = self.get(key)
        if item is None:
            return False
        item.pinned = not item.pinned
        self.save()
        return item.pinned

    def remove(self, key: str) -> None:
        self._items = [i for i in self._items if i.key != key]
        self.save()

    def clear_unpinned(self) -> None:
        self._items = [i for i in self._items if i.pinned]
        self.save()

    def _trim(self) -> None:
        unpinned = [i for i in self._items if not i.pinned]
        if len(unpinned) <= self.limit:
            return
        keep = sorted(unpinned, key=lambda i: -i.ts)[: self.limit]
        keep_ids = {id(i) for i in keep}
        self._items = [i for i in self._items if i.pinned or id(i) in keep_ids]
