"""应用快开：直接列出开始菜单里的应用，点一下就启动。

不再让用户自己进安装目录找 exe。列表来自当前用户和公共开始菜单里的 .lnk，
外加少量系统自带命令；只读文件名，不解析快捷方式内容，也不联网。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QLineEdit, QWidget

from .panels import SUBTEXT, TEXT, PopupCard

PAD = 14
WIDTH = 460
HEIGHT = 560
ROW_H = 32
BODY_W = WIDTH - PAD * 2       # 432
BODY_H = HEIGHT - 84 - 34      # 442

_SKIP_WORDS = (
    "uninstall", "卸载", "remove", "readme", "help", "帮助", "documentation",
    "website", "网址", "官网", "license", "许可", "update", "更新", "修复",
    "repair", "diagnostic", "诊断", "release notes", "generate", "add a",
    "delete all", "virtual ethernet", "watchdog", "administrative tools",
    "accessories", "system tools",
)
# 常用应用置顶；命中后按这里的顺序排，其余中文在前、英文在后
_PRIORITY = (
    "微信", "企业微信", "飞书", "钉钉", "QQ", "Google Chrome",
    "Visual Studio Code", "DBeaver", "Apifox", "Excel", "Word", "PowerPoint",
    "Windows Terminal", "Git Bash", "Bandizip",
)
_priority_map = {name.casefold(): i for i, name in enumerate(_PRIORITY)}


@dataclass(frozen=True)
class AppEntry:
    name: str
    target: str
    kind: str          # "lnk" 或 "exe"


def _start_menu_dirs() -> list[Path]:
    dirs = []
    appdata = os.environ.get("APPDATA")
    programdata = os.environ.get("PROGRAMDATA")
    if appdata:
        dirs.append(Path(appdata) / "Microsoft/Windows/Start Menu/Programs")
    if programdata:
        dirs.append(Path(programdata) / "Microsoft/Windows/Start Menu/Programs")
    return dirs


def _skip(name: str) -> bool:
    low = name.casefold()
    return any(w in low for w in _SKIP_WORDS)


def _sort_key(entry: AppEntry):
    low = entry.name.casefold()
    if low in _priority_map:
        return (0, _priority_map[low], "")
    return (1, not entry.name[:1].isascii(), low)


_cache: list[AppEntry] = []
_cache_at = 0.0


def discover_apps(force: bool = False) -> list[AppEntry]:
    """扫开始菜单；同名只留第一条（当前用户优先），结果缓存。"""
    global _cache, _cache_at
    if not force and _cache and time.monotonic() - _cache_at < 60:
        return _cache

    found: dict[str, AppEntry] = {}
    for root in _start_menu_dirs():
        if not root.is_dir():
            continue
        try:
            files = sorted(root.rglob("*.lnk"),
                           key=lambda p: (len(p.parts), p.name.casefold()))
        except OSError:
            continue
        for path in files:
            name = path.stem.strip()
            if not name or _skip(name):
                continue
            low_path = str(path).casefold()
            if ("\\startup\\" in low_path or "\\utilities\\" in low_path
                    or "administrative tools\\" in low_path):
                continue
            key = name.casefold()
            if key in found:
                continue
            found[key] = AppEntry(name=name[:36], target=str(path), kind="lnk")

    # 常见但开始菜单里可能没有直接入口的系统工具，补几条
    extras = (
        ("文件资源管理器", "explorer.exe"),
        ("记事本", "notepad.exe"),
        ("计算器", "calc.exe"),
        ("画图", "mspaint.exe"),
        ("Windows Terminal", shutil.which("wt.exe") or ""),
        ("PowerShell", shutil.which("powershell.exe") or ""),
    )
    for name, target in extras:
        if target and all(name.casefold() != e.name.casefold() for e in found.values()):
            found[name.casefold()] = AppEntry(name=name, target=target, kind="exe")

    _cache = sorted(found.values(), key=_sort_key)
    _cache_at = time.monotonic()
    return _cache


class AppList(QWidget):
    """整块自绘的列表：不为每一行建 QWidget，首开不会卡。"""

    activated = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.entries: list[AppEntry] = []
        self.filtered: list[AppEntry] = []
        self._hover = -1
        self._offset = 0
        self._drag = False
        self.setFixedSize(BODY_W, BODY_H)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("background:transparent;")

    def set_entries(self, entries: list[AppEntry]) -> None:
        self.entries = list(entries)
        self.filtered = list(entries)
        self._offset = 0
        self._hover = -1
        self.update()

    def set_filter(self, text: str) -> None:
        q = text.strip().casefold()
        if not q:
            self.filtered = list(self.entries)
        else:
            self.filtered = [e for e in self.entries
                             if q in e.name.casefold() or q in e.target.casefold()]
        self._hover = -1
        self._offset = 0
        self.update()

    def _content_h(self) -> int:
        n = len(self.filtered)
        return ROW_H * n + 2 * max(0, n - 1) + 4

    def _max_offset(self) -> int:
        return max(0, self._content_h() - self.height())

    def _clamp(self) -> None:
        self._offset = max(0, min(self._offset, self._max_offset()))

    def _row_at(self, vy: int) -> int:
        y = vy + self._offset
        if y < 0:
            return -1
        idx = int(y // (ROW_H + 2))
        if 0 <= idx < len(self.filtered) and y < idx * (ROW_H + 2) + ROW_H:
            return idx
        return -1

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta:
            self._offset -= int(delta / 120) * (ROW_H + 2) * 3
            self._clamp()
            self._hover = self._row_at(int(event.position().y()))
            self.update()
            event.accept()
            return
        super().wheelEvent(event)

    def _scrollbar_h(self) -> int:
        total = self._content_h()
        if total <= self.height():
            return 0
        track_h = max(1, self.height() - 4)
        return max(24, int(track_h * self.height() / total))

    def _scrollbar_track(self) -> QRect:
        return QRect(self.width() - 6, 2, 4, max(1, self.height() - 4))

    def _set_offset_from_scrollbar(self, y: int) -> None:
        track = self._scrollbar_track()
        h = self._scrollbar_h()
        span = max(1, track.height() - h)
        self._offset = int(max(0, min(self._max_offset(),
                                      (y - track.y() - h / 2) / span * self._max_offset())))
        self.update()

    def mousePressEvent(self, event) -> None:
        if (event.button() == Qt.LeftButton
                and event.position().x() >= self.width() - 12
                and self._content_h() > self.height()):
            self._drag = True
            self._set_offset_from_scrollbar(int(event.position().y()))
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag:
            self._set_offset_from_scrollbar(int(event.position().y()))
            return
        idx = self._row_at(int(event.position().y()))
        if idx != self._hover:
            self._hover = idx
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag:
            self._drag = False
            return
        if event.button() == Qt.LeftButton:
            idx = self._row_at(int(event.position().y()))
            if idx >= 0:
                self.activated.emit(self.filtered[idx])
                return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(self.font())
        f.setPixelSize(12)
        p.setFont(f)
        fm = QFontMetrics(f)
        step = ROW_H + 2
        start = max(0, self._offset // step)
        for i in range(start, len(self.filtered)):
            y = i * step - self._offset
            if y >= self.height():
                break
            if y + ROW_H < 0:
                continue
            entry = self.filtered[i]
            if self._hover == i:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 16))
                p.drawRoundedRect(QRect(0, y + 1, self.width() - 8, ROW_H - 2), 8, 8)
            p.setPen(QColor(TEXT))
            p.drawText(QRect(10, y, self.width() - 34, ROW_H),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(entry.name, Qt.ElideRight, self.width() - 42))
            p.setFont(QFont(self.font()))
            p.setPen(QColor(SUBTEXT))
            p.drawText(QRect(self.width() - 22, y, 16, ROW_H),
                       Qt.AlignCenter, "›")
            p.setFont(f)

        total = self._content_h()
        if total > self.height():
            track = QRect(self.width() - 6, 2, 4, self.height() - 4)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#2c2c2e"))
            p.drawRoundedRect(track, 2, 2)
            h = max(24, int(track.height() * self.height() / total))
            span = max(1, self._max_offset())
            top = track.y() + int((track.height() - h) * self._offset / span)
            p.setBrush(QColor("#636366"))
            p.drawRoundedRect(QRect(track.x(), top, 4, h), 2, 2)
        p.end()


class AppPanel(PopupCard):
    WIDTH = WIDTH
    projects_requested = Signal()

    def __init__(self) -> None:
        super().__init__(height=HEIGHT)
        self._items = discover_apps()
        self._hot = ""
        self._status = ""
        self._status_until = 0.0

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("搜索应用，例如 微信 / chrome / code")
        self.search.setGeometry(PAD, 46, self.width() - PAD * 2, 30)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._launch_first)
        self.search.setStyleSheet(
            "QLineEdit{background:#141416;color:#e5e5ea;border:1px solid #2c2c2e;"
            "border-radius:8px;padding:0 9px;font-size:12px;}"
            "QLineEdit:focus{border-color:#0a84ff;}")

        self.list = AppList(self)
        self.list.setGeometry(PAD, 84, BODY_W, BODY_H)
        self.list.activated.connect(self._launch)
        self.list.set_entries(self._items)

        self._close_rect = QRect(self.width() - 38, 12, 24, 24)
        self._refresh_rect = QRect(self.width() - 38 - 6 - 46, 12, 46, 24)
        self._proj_rect = QRect(self.width() - 38 - 6 - 46 - 6 - 46, 12, 46, 24)

    def _filter(self, text: str) -> None:
        self.list.set_filter(text)
        if not self.list.filtered:
            self._show_status("没有匹配的应用，试试更短的关键词")
        elif self._status.startswith("没有匹配"):
            self._status = ""
            self.update()

    def reset_search(self) -> None:
        self.search.clear()
        self._filter("")

    def _launch_first(self) -> None:
        if self.list.filtered:
            self._launch(self.list.filtered[0])

    def refresh_apps(self) -> None:
        self._items = discover_apps(force=True)
        self.list.set_entries(self._items)
        self.reset_search()
        self._show_status("已重新扫描开始菜单，共 %d 个应用" % len(self._items))

    def _show_status(self, text: str) -> None:
        self._status = text
        self._status_until = time.monotonic() + 3.0
        self.update()
        QTimer.singleShot(3000, self._clear_status)

    def _clear_status(self) -> None:
        if time.monotonic() >= self._status_until:
            self._status = ""
            self.update()

    def _launch(self, entry: AppEntry) -> None:
        try:
            if entry.kind == "lnk":
                os.startfile(entry.target)
            else:
                subprocess.Popen([entry.target])
        except OSError:
            self._show_status("启动失败：%s" % entry.name)
            return
        self.close_panel()

    def mouseMoveEvent(self, event) -> None:
        hot = self._hit(event.position().toPoint())
        if hot != self._hot:
            self._hot = hot
            self.setCursor(Qt.PointingHandCursor if hot else Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hot = ""
        self.setCursor(Qt.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def _hit(self, pos) -> str:
        if self._close_rect.contains(pos):
            return "close"
        if self._refresh_rect.contains(pos):
            return "refresh"
        if self._proj_rect.contains(pos):
            return "projects"
        return ""

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            hit = self._hit(event.position().toPoint())
            if hit == "close":
                self.close_panel()
                return
            if hit == "refresh":
                self.refresh_apps()
                return
            if hit == "projects":
                self.projects_requested.emit()
                self.hide()
                return
        super().mouseReleaseEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.list.setFixedWidth(BODY_W)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(self.font())
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(TEXT))
        p.drawText(QRect(PAD, 8, 220, 28), Qt.AlignLeft | Qt.AlignVCenter, "应用快开")

        for rect, label, key in ((self._proj_rect, "项目", "projects"),
                                 (self._refresh_rect, "刷新", "refresh")):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#3a3a3c" if self._hot == key else "#2c2c2e"))
            p.drawRoundedRect(rect, 8, 8)
            p.setFont(self._font(11))
            p.setPen(QColor("#ffffff" if self._hot == key else "#d1d1d6"))
            p.drawText(rect, Qt.AlignCenter, label)

        p.setFont(self._font(11))
        p.setPen(QColor("#ffffff" if self._hot == "close" else SUBTEXT))
        p.drawText(self._close_rect, Qt.AlignCenter, "✕")

        foot_y = self.height() - 24
        p.setFont(self._font(9))
        p.setPen(QColor("#ff9f0a" if self._status and time.monotonic() < self._status_until
                        else SUBTEXT))
        text = self._status or "单击应用直接启动 · 输入关键词过滤 · 数据来自开始菜单"
        p.drawText(QRect(PAD, foot_y, self.width() - PAD * 2, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, text)
        p.end()

    def _font(self, px: int) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(px)
        return f
