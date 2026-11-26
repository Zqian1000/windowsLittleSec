"""剪贴板历史：文字和图片都存，固定项不会被挤掉，落盘到配置目录。

对齐 Windows 自带剪贴板（Win+V）的核心能力：
- 文字、图片都能进历史；
- 图片原样落盘，列表里显示缩略图；
- 固定项置顶保存，不占最近 N 条的名额；
- 删除/清空时顺手把图片副本删掉，不在配置目录里留垃圾。

写入前由 `island/clipboard.py` 判断内容是否被密码管理器标记成"禁止采集"。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from core import config

# 单条上限，超长文本（日志、代码整段）截断保存，避免历史文件失控
MAX_TEXT = 20_000
IMAGE_DIR = "clip_images"


@dataclass
class ClipItem:
    text: str = ""
    ts: float = 0.0
    pinned: bool = False
    kind: str = "text"          # text / image
    image: str = ""             # 图片副本的本地路径
    width: int = 0
    height: int = 0
    digest: str = ""            # 去重用的短哈希，落盘后保持稳定

    @property
    def key(self) -> str:
        if self.digest:
            return self.digest
        return hashlib.md5(self.text.encode("utf-8", "ignore")).hexdigest()[:16]

    @property
    def is_image(self) -> bool:
        return self.kind == "image"

    def preview(self, lines: int = 2, width: int = 46) -> str:
        """压成两三行给界面用，换行和连续空白都折叠掉。"""
        if self.is_image:
            size = f"{self.width} × {self.height}" if self.width and self.height else "图片"
            return f"图片 · {size}"
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
        return {"text": self.text, "ts": self.ts, "pinned": self.pinned,
                "kind": self.kind, "image": self.image,
                "width": self.width, "height": self.height,
                "digest": self.digest}


class ClipStore:
    """最近 N 条剪贴板记录（文字/图片）；固定的条目单独保留，不占 N 的名额。"""

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
                image = str(row.get("image", ""))
                kind = str(row.get("kind", "image" if image else "text"))
                if kind == "image":
                    if not image or not Path(image).exists():
                        continue
                    items.append(ClipItem(
                        text=text, ts=float(row.get("ts", 0)),
                        pinned=bool(row.get("pinned", False)),
                        kind="image", image=image,
                        width=int(row.get("width", 0) or 0),
                        height=int(row.get("height", 0) or 0),
                        digest=str(row.get("digest", "")) or
                        "img_" + hashlib.sha1(Path(image).read_bytes()).hexdigest()[:16],
                    ))
                else:
                    if not text:
                        continue
                    items.append(ClipItem(
                        text=text[:MAX_TEXT], ts=float(row.get("ts", 0)),
                        pinned=bool(row.get("pinned", False)),
                        kind="text",
                        digest=str(row.get("digest", "")),
                    ))
            except (OSError, TypeError, ValueError):
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
        item = ClipItem(text=text, ts=time.time(), pinned=pinned, kind="text")
        item.digest = hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()[:16]
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

    def add_image_bytes(self, data: bytes, width: int = 0,
                        height: int = 0, pinned: bool = False) -> ClipItem | None:
        """把一张 PNG 的原始字节存进历史；同图只更新时间，不重复落盘。"""
        if not data:
            return None
        digest = "img_" + hashlib.sha1(data).hexdigest()[:16]
        item = ClipItem(ts=time.time(), pinned=pinned, kind="image",
                        width=int(width), height=int(height), digest=digest)
        old = self.get(item.key)
        if old is not None:
            old.ts = item.ts
            old.pinned = old.pinned or pinned
            self.save()
            return old
        try:
            folder = config.config_dir() / IMAGE_DIR
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{digest}.png"
            if not path.exists():
                path.write_bytes(data)
            item.image = str(path)
        except OSError:
            return None
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

    def touch(self, key: str) -> None:
        """点选旧条目后把它顶到最近：只改时间，不重复写文件。"""
        item = self.get(key)
        if item is None:
            return
        item.ts = time.time()
        self.save()

    def remove(self, key: str) -> None:
        item = self.get(key)
        if item is not None:
            self._delete_image(item)
        self._items = [i for i in self._items if i.key != key]
        self.save()

    def clear_unpinned(self) -> None:
        for item in self._items:
            if not item.pinned:
                self._delete_image(item)
        self._items = [i for i in self._items if i.pinned]
        self.save()

    def _delete_image(self, item: ClipItem) -> None:
        if not item.is_image or not item.image:
            return
        try:
            p = Path(item.image)
            if (config.config_dir() / IMAGE_DIR) in p.parents:
                p.unlink(missing_ok=True)
        except OSError:
            pass

    def _trim(self) -> None:
        unpinned = [i for i in self._items if not i.pinned]
        if len(unpinned) <= self.limit:
            return
        keep = sorted(unpinned, key=lambda i: -i.ts)[: self.limit]
        keep_ids = {id(i) for i in keep}
        dropped = [i for i in self._items if not i.pinned and id(i) not in keep_ids]
        for item in dropped:
            self._delete_image(item)
        self._items = [i for i in self._items if i.pinned or id(i) in keep_ids]
