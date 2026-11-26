"""网站收藏：名称、网址和本地持久化，不碰界面。

数据放在 %APPDATA%\\Xiaoer\\favorites.json。首次使用给一组常用站点，
之后完全以用户增删为准；删空后也不会把默认项自动加回来。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from core import config

MAX_NAME = 40
MAX_URL = 2048

DEFAULT_SITES = (
    ("豆包", "https://www.doubao.com"),
    ("飞书", "https://www.feishu.cn"),
    ("掘金", "https://juejin.cn"),
    ("Bilibili", "https://www.bilibili.com"),
    ("GitHub", "https://github.com"),
    ("少数派", "https://sspai.com"),
)


@dataclass
class Website:
    id: str
    name: str
    url: str
    created: float = 0.0
    starred: bool = False

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "url": self.url,
                "created": self.created, "starred": self.starred}

    @classmethod
    def from_json(cls, row: dict) -> "Website | None":
        try:
            url = normalize_url(str(row.get("url", "")))
        except ValueError:
            return None
        name = str(row.get("name", "")).strip()[:MAX_NAME] or host_label(url)
        return cls(
            id=str(row.get("id") or uuid.uuid4().hex[:10]),
            name=name,
            url=url,
            created=float(row.get("created") or 0.0),
            starred=bool(row.get("starred", False)),
        )

    @property
    def host(self) -> str:
        return urlparse(self.url).netloc.lower()

    @property
    def display_url(self) -> str:
        """列表里只显示 host + 路径，太长时截断。"""
        parsed = urlparse(self.url)
        tail = parsed.path.rstrip("/")
        text = parsed.netloc + tail
        return text if len(text) <= 44 else text[:41] + "…"


def normalize_url(raw: str) -> str:
    """只接受 http/https；没写协议时默认 https。"""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("网址不能为空")
    if "://" in text:
        scheme = text.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            raise ValueError("只支持 http/https 网址")
    else:
        # 防止 javascript:/data:/file: 这类协议被自动补成 https。
        prefix = text.split(":", 1)[0].lower()
        if prefix in ("javascript", "data", "file", "vbscript", "about", "blob",
                      "mailto") and ":" in text:
            raise ValueError("只支持 http/https 网址")
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise ValueError("只支持 http/https 网址")
    try:
        host = parsed.hostname
        parsed.port  # 端口非法（如 javascript:alert(1)）会在这里抛 ValueError
    except ValueError as exc:
        raise ValueError("网址格式不正确") from exc
    if not host or any(ch in host for ch in " /\\()[]{}<>\"'`"):
        raise ValueError("网址格式不正确")
    if len(text) > MAX_URL:
        raise ValueError("网址太长")
    return text


def host_label(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return (host or "网站")[:MAX_NAME]


def parse_entry(text: str) -> tuple[str, str]:
    """支持三种输入：网址 / ``名称 网址`` / ``名称|网址``。"""
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("请输入网址")
    if "|" in raw:
        name, url = raw.split("|", 1)
        name, url = name.strip(), url.strip()
    else:
        parts = raw.split()
        if len(parts) >= 2 and ("://" in parts[-1] or "." in parts[-1]):
            name, url = " ".join(parts[:-1]), parts[-1]
        else:
            name, url = "", raw
    url = normalize_url(url)
    name = (name or host_label(url)).strip()[:MAX_NAME]
    return name, url


class FavoriteStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (config.config_dir() / "favorites.json")
        self._items: list[Website] = []
        self.load()

    def load(self) -> None:
        if not self._path.exists():
            self._items = [Website(uuid.uuid4().hex[:10], n, u, time.time())
                           for n, u in DEFAULT_SITES]
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._items = []
            return
        rows = raw.get("items", []) if isinstance(raw, dict) else []
        self._items = []
        for row in rows:
            item = Website.from_json(row) if isinstance(row, dict) else None
            if item is not None:
                self._items.append(item)

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"items": [i.to_json() for i in self._items]},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            pass

    def items(self) -> list[Website]:
        """星标优先；同组内保持添加顺序。"""
        return sorted(self._items, key=lambda item: not item.starred)

    def add(self, text: str) -> tuple[Website, bool]:
        """新增或按同一网址更新名称；返回 (条目, 是否新建)。"""
        name, url = parse_entry(text)
        for item in self._items:
            if item.url.rstrip("/").lower() == url.rstrip("/").lower():
                item.name = name or item.name
                self.save()
                return item, False
        item = Website(uuid.uuid4().hex[:10], name, url, time.time())
        self._items.insert(0, item)
        self.save()
        return item, True

    def remove(self, item_id: str) -> bool:
        for index, item in enumerate(self._items):
            if item.id == item_id:
                del self._items[index]
                self.save()
                return True
        return False

    def toggle_star(self, item_id: str) -> bool | None:
        """切换星标；返回新的星标状态，找不到时返回 None。"""
        for item in self._items:
            if item.id == item_id:
                item.starred = not item.starred
                self.save()
                return item.starred
        return None
