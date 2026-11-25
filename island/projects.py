"""项目快开：常用工作目录，一键用 VS Code / 资源管理器 / 终端打开。

列表存在 `%APPDATA%\\Xiaoer\\projects.json`，第一次启动会放入当前项目目录。
面板全部自绘（不建 QLabel/QPushButton），点开时不会因为控件首次初始化而卡。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QFileDialog

from core import config
from .panels import ACCENT, BG, SUBTEXT, TEXT, PopupCard

ROW_H = 40
HEAD_H = 48
MAX_ROWS = 7
FOOT_H = 22
PAD = 14

# (动作 key, 标签)
ACTIONS = (("code", "代码"), ("explorer", "目录"), ("terminal", "终端"))
ACTION_W = 40
ACTION_H = 20
ACTION_GAP = 4


def projects_path() -> Path:
    return config.config_dir() / "projects.json"


def load_projects() -> list[dict]:
    path = projects_path()
    if not path.exists():
        root = Path(__file__).resolve().parent.parent
        data = [{"name": "xiaoer", "path": str(root)}]
        save_projects(data)
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    for row in raw if isinstance(raw, list) else []:
        try:
            name = str(row.get("name", "")).strip()
            p = str(row.get("path", "")).strip()
        except AttributeError:
            continue
        if name and p:
            out.append({"name": name[:24], "path": p})
    return out


def save_projects(items: list[dict]) -> None:
    try:
        projects_path().write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _code_cmd() -> str | None:
    found = shutil.which("code") or shutil.which("code.cmd")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    for rel in ("Programs/Microsoft VS Code/bin/code.cmd",
                "Programs/Microsoft VS Code/bin/code.exe"):
        p = Path(local) / rel
        if p.exists():
            return str(p)
    return None


def _open_code(path: str) -> bool:
    code = _code_cmd()
    if not code:
        return False
    try:
        subprocess.Popen(subprocess.list2cmdline([code, path]), shell=True)
        return True
    except OSError:
        return False


def _open_explorer(path: str) -> bool:
    try:
        subprocess.Popen(["explorer.exe", path])
        return True
    except OSError:
        return False


def _open_terminal(path: str) -> bool:
    try:
        wt = shutil.which("wt")
        if wt:
            subprocess.Popen([wt, "-d", path])
        else:
            subprocess.Popen([
                "powershell.exe", "-NoExit", "-Command",
                "Set-Location -LiteralPath '%s'" % path.replace("'", "''"),
            ])
        return True
    except OSError:
        return False


class ProjectPanel(PopupCard):
    WIDTH = 420

    def __init__(self) -> None:
        self._items = load_projects()
        self._hot_row = -1
        self._hot_action = ""
        self._status = ""
        self._status_until = 0.0
        super().__init__(height=self._height_for(len(self._items)))
        self._relayout()

    def _height_for(self, n: int) -> int:
        return HEAD_H + max(1, min(n, MAX_ROWS)) * ROW_H + FOOT_H + 12

    def reload(self) -> None:
        """外部改了 projects.json 后，下次打开重新读一遍。"""
        self._items = load_projects()
        self._relayout()

    def _relayout(self) -> None:
        self.setFixedHeight(self._height_for(len(self._items)))
        self._add_rect = QRect(self.width() - PAD - 58, 12, 58, 24)
        self._cfg_rect = QRect(self.width() - PAD - 58 - 48, 12, 44, 24)
        self._row_rects = [QRect(PAD, HEAD_H + i * ROW_H,
                                 self.width() - PAD * 2, ROW_H - 2)
                           for i in range(min(len(self._items), MAX_ROWS))]
        self.update()

    def _action_rects(self, row: int) -> dict[str, QRect]:
        r = self._row_rects[row]
        total = len(ACTIONS) * ACTION_W + (len(ACTIONS) - 1) * ACTION_GAP
        x = r.right() - total
        y = r.top() + (r.height() - ACTION_H) // 2
        out = {}
        for i, (key, _label) in enumerate(ACTIONS):
            out[key] = QRect(x + i * (ACTION_W + ACTION_GAP), y, ACTION_W, ACTION_H)
        return out

    def _hit(self, pos) -> tuple[int, str]:
        if self._add_rect.contains(pos):
            return -1, "add"
        if self._cfg_rect.contains(pos):
            return -1, "cfg"
        for i, r in enumerate(self._row_rects):
            if not r.contains(pos):
                continue
            for key, rect in self._action_rects(i).items():
                if rect.contains(pos):
                    return i, key
            return i, "default"
        return -1, ""

    def mouseMoveEvent(self, event) -> None:
        row, action = self._hit(event.position().toPoint())
        if row != self._hot_row or action != self._hot_action:
            self._hot_row, self._hot_action = row, action
            self.setCursor(Qt.PointingHandCursor if action else Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hot_row, self._hot_action = -1, ""
        self.setCursor(Qt.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            row, action = self._hit(event.position().toPoint())
            if action == "add":
                self._add_folder()
                return
            if action == "cfg":
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(projects_path())))
                return
            if row >= 0 and row < len(self._items):
                key = "code" if action == "default" else action
                self._launch(self._items[row], key)
                return
        super().mouseReleaseEvent(event)

    def _add_folder(self) -> None:
        self.auto_dismiss = False
        try:
            path = QFileDialog.getExistingDirectory(self, "选择项目目录")
        finally:
            self.auto_dismiss = True
        if not path:
            return
        name = Path(path).name or path
        for i, item in enumerate(self._items):
            if os.path.normcase(item["path"]) == os.path.normcase(path):
                self._items.pop(i)
                break
        self._items.insert(0, {"name": name[:24], "path": path})
        self._items = self._items[:MAX_ROWS * 2]
        save_projects(self._items)
        self._relayout()

    def _show_status(self, text: str) -> None:
        self._status = text
        self._status_until = time.monotonic() + 3.0
        self.update()
        QTimer.singleShot(3000, self._clear_status)

    def _clear_status(self) -> None:
        if time.monotonic() >= self._status_until:
            self._status = ""
            self.update()

    def _launch(self, item: dict, action: str) -> None:
        path = item.get("path", "")
        if not os.path.isdir(path):
            self._show_status("目录不存在：%s" % path)
            return
        ok = False
        if action == "code":
            ok = _open_code(path)
            if not ok:
                ok = _open_explorer(path)
        elif action == "terminal":
            ok = _open_terminal(path)
        else:
            ok = _open_explorer(path)
        if ok:
            self.close_panel()
        else:
            self._show_status("没找到对应的启动程序")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        title = QFont(self.font())
        title.setPixelSize(15)
        title.setBold(True)
        p.setFont(title)
        p.setPen(QColor(TEXT))
        p.drawText(QRect(PAD, 8, 160, 28), Qt.AlignLeft | Qt.AlignVCenter, "项目快开")

        # 头部按钮
        for rect, label, hot in ((self._add_rect, "+ 添加", self._hot_action == "add"),
                                 (self._cfg_rect, "配置", self._hot_action == "cfg")):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#3a3a3c" if hot else "#2c2c2e"))
            p.drawRoundedRect(rect, 8, 8)
            p.setFont(self._font(11))
            p.setPen(QColor("#ffffff" if hot else "#d1d1d6"))
            p.drawText(rect, Qt.AlignCenter, label)

        if not self._items:
            p.setFont(self._font(12))
            p.setPen(QColor(SUBTEXT))
            p.drawText(QRect(PAD, HEAD_H, self.width() - PAD * 2, ROW_H),
                       Qt.AlignCenter, "还没有项目，点右上角「+ 添加」选一个目录")

        for i, item in enumerate(self._items[:MAX_ROWS]):
            r = self._row_rects[i]
            hot_row = self._hot_row == i
            if hot_row:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 14))
                p.drawRoundedRect(r, 9, 9)

            name = item.get("name") or Path(item.get("path", "")).name
            path = item.get("path", "")
            exists = os.path.isdir(path)
            nf = self._font(12)
            nf.setBold(True)
            p.setFont(nf)
            p.setPen(QColor(TEXT if exists else "#8e8e93"))
            p.drawText(QRect(r.left() + 9, r.top() + 4, 190, 16),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(nf).elidedText(name, Qt.ElideRight, 186))

            pf = self._font(9)
            p.setFont(pf)
            p.setPen(QColor(SUBTEXT if exists else "#636366"))
            p.drawText(QRect(r.left() + 9, r.top() + 20, 190, 14),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(pf).elidedText(path, Qt.ElideMiddle, 186))

            for key, label in ACTIONS:
                ar = self._action_rects(i)[key]
                hot = hot_row and self._hot_action == key
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(ACCENT if hot else "#2c2c2e"))
                p.drawRoundedRect(ar, 7, 7)
                p.setFont(self._font(9))
                p.setPen(QColor("#ffffff" if hot else "#c7c7cc"))
                p.drawText(ar, Qt.AlignCenter, label)

        foot_y = self.height() - FOOT_H - 4
        p.setFont(self._font(9))
        if self._status and time.monotonic() < self._status_until:
            p.setPen(QColor("#ff9f0a"))
        else:
            p.setPen(QColor(SUBTEXT))
            self._status = "点项目名用 VS Code 打开；右侧可换目录/终端"
        p.drawText(QRect(PAD, foot_y, self.width() - PAD * 2, FOOT_H),
                   Qt.AlignLeft | Qt.AlignVCenter, self._status)
        p.end()

    def _font(self, px: int) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(px)
        return f
