"""桌面小伙伴的本地档案：照片、位置、是否显示，全部留在本机。

只存数据，不碰界面；照片的卡通化在 island/companion_ui.py 里做。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from core import config


class CompanionProfile:
    """%APPDATA%\\Xiaoer\\companion.json：一张照片 + 小人停在哪。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or (config.config_dir() / "companion.json")
        self._data: dict = {
            "face": "",       # 卡通化后的正脸图（配置目录内，删了可重生成）
            "source": "",     # 用户拖进来的原图路径，仅作记录
            "sprite_source": "",   # 这套四帧小人是对着哪张图生成的
            "pos": None,      # [x, y]，None 表示首次摆到右下角
            "visible": True,
        }
        self.load()

    # ---- 磁盘 ----
    def load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        self._data["face"] = str(raw.get("face", "") or "")
        self._data["source"] = str(raw.get("source", "") or "")
        self._data["sprite_source"] = str(raw.get("sprite_source", "") or "")
        pos = raw.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                self._data["pos"] = [int(pos[0]), int(pos[1])]
            except (TypeError, ValueError):
                self._data["pos"] = None
        self._data["visible"] = bool(raw.get("visible", True))

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            pass

    # ---- 读 ----
    @property
    def face(self) -> str:
        """卡通脸文件；文件被删了就当没有。"""
        path = self._data.get("face") or ""
        return path if path and Path(path).exists() else ""

    @property
    def source(self) -> str:
        return self._data.get("source") or ""

    @property
    def sprite_source(self) -> str:
        return self._data.get("sprite_source") or ""

    @property
    def visible(self) -> bool:
        return bool(self._data.get("visible", True))

    @property
    def pos(self) -> Optional[list[int]]:
        pos = self._data.get("pos")
        return list(pos) if pos else None

    @property
    def face_dir(self) -> Path:
        """跟 companion.json 放同一层：测试换路径时不会写到真实配置里。"""
        d = self._path.parent / "companion"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def face_target(self) -> Path:
        return self.face_dir / "face.png"

    # ---- 写 ----
    def set_face(self, face_path: str, source: str) -> None:
        self._data["face"] = str(face_path)
        self._data["source"] = str(source)
        if self._data.get("sprite_source") and self._data["sprite_source"] != str(source):
            self._data["sprite_source"] = ""     # 新照片，旧的四帧小人不再匹配
        self.save()

    def set_sprite(self, source: str) -> None:
        self._data["sprite_source"] = str(source)
        self.save()

    def sprite_frames(self) -> list[Path]:
        """豆包生成的四帧（站好/举手×2/双手举），和当前照片对得上才用。"""
        if not self._data.get("sprite_source") or self._data["sprite_source"] != self.source:
            return []
        frames = [self.face_dir / f"sprite_{i}.png" for i in range(1, 5)]
        return frames if all(p.exists() for p in frames) else []

    def clear_face(self) -> None:
        face = self.face
        self._data["face"] = ""
        self._data["source"] = ""
        if face:
            try:
                Path(face).unlink(missing_ok=True)
            except OSError:
                pass
        self.save()

    def set_visible(self, visible: bool) -> None:
        self._data["visible"] = bool(visible)
        self.save()

    def set_pos(self, x: int, y: int) -> None:
        self._data["pos"] = [int(x), int(y)]
        self.save()
