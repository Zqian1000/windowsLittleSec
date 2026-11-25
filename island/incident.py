"""报错现场包：把前台窗口、进程、时间和剪贴板里的报错拼成 Markdown。

只读本机信息，不截屏、不联网。先给预览，用户确认后再复制，避免把剪贴板里的
密码/密钥直接带进要发出去的文本里。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import socket
import time

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QPlainTextEdit

from .panels import ACCENT, SUBTEXT, TEXT, PopupCard

MAX_CLIP = 6000


def _foreground_info() -> tuple[str, str]:
    """返回 (窗口标题, 进程名)；取不到就给空串，不让整包失败。"""
    try:
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = ctypes.c_void_p
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return "", ""
        length = u.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
        buf = ctypes.create_unicode_buffer(length + 1)
        u.GetWindowTextW(ctypes.c_void_p(hwnd), buf, length + 1)
        title = buf.value.strip()

        pid = wt.DWORD()
        u.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        name = ""
        try:
            import psutil
            name = psutil.Process(pid.value).name()
        except Exception:
            pass
        return title, name
    except Exception:
        return "", ""


class IncidentPanel(PopupCard):
    WIDTH = 460
    copied = Signal(str)

    def __init__(self, clip_watcher=None) -> None:
        super().__init__(height=372)
        self.clip_watcher = clip_watcher
        self._all = ""
        self._no_clip = ""
        self._clip_note = ""
        self._hot = ""

        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFocusPolicy(Qt.NoFocus)
        self.text.setGeometry(14, 52, self.width() - 28, 260)
        self.text.setStyleSheet(
            "QPlainTextEdit{background:#141416;color:#e5e5ea;border:1px solid #2c2c2e;"
            "border-radius:10px;padding:8px;font-size:11px;"
            "selection-background-color:#0a84ff;}"
            "QScrollBar:vertical{width:7px;background:#141416;}"
            "QScrollBar::handle:vertical{background:#3a3a3c;border-radius:3px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")

        self._close_rect = QRect(self.width() - 38, 12, 24, 24)
        self._copy_rect = QRect(14, self.height() - 44, 142, 30)
        self._copy_bare_rect = QRect(164, self.height() - 44, 108, 30)

    def refresh(self) -> None:
        title, proc = _foreground_info()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        host = socket.gethostname()
        clip = QGuiApplication.clipboard().text().strip()
        truncated = len(clip) > MAX_CLIP
        clip = clip[:MAX_CLIP]
        self._clip_note = ("剪贴板文本（已截断到 %d 字）" % MAX_CLIP
                           if truncated else "剪贴板文本")
        head = (f"### 报错现场\n"
                f"- 时间：{now}\n"
                f"- 主机：{host}\n"
                f"- 前台窗口：{title or '（取不到）'}\n"
                f"- 进程：{proc or '（取不到）'}\n")
        self._no_clip = head
        if clip:
            self._all = (head + f"- {self._clip_note}：\n\n"
                         "~~~~text\n" + clip + "\n~~~~\n")
        else:
            self._all = head + "\n（剪贴板里没有文字）\n"
        self.text.setPlainText(self._all)

    def _copy(self, content: str) -> None:
        if not content:
            return
        if self.clip_watcher is not None:
            self.clip_watcher.mark_self_write()
        QGuiApplication.clipboard().setText(content)
        self.hide()
        self.copied.emit("报错现场已复制，可直接粘贴")

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
        if self._copy_rect.contains(pos):
            return "copy"
        if self._copy_bare_rect.contains(pos):
            return "bare"
        return ""

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            hit = self._hit(event.position().toPoint())
            if hit == "close":
                self.close_panel()
                return
            if hit == "copy":
                self._copy(self._all)
                return
            if hit == "bare":
                self._copy(self._no_clip)
                return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(self.font())
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(TEXT))
        p.drawText(QRect(14, 8, 240, 28), Qt.AlignLeft | Qt.AlignVCenter, "报错现场包")

        p.setFont(self._font(11))
        p.setPen(QColor("#ffffff" if self._hot == "close" else SUBTEXT))
        p.drawText(self._close_rect, Qt.AlignCenter, "✕")

        self._button(p, self._copy_rect, "复制（含剪贴板）", self._hot == "copy", True)
        self._button(p, self._copy_bare_rect, "仅窗口信息", self._hot == "bare", False)
        p.setFont(self._font(9))
        p.setPen(QColor(SUBTEXT))
        p.drawText(QRect(282, self.height() - 40, 164, 24),
                   Qt.AlignRight | Qt.AlignVCenter, "先预览，再决定复制")
        p.end()

    def _font(self, px: int) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(px)
        return f

    def _button(self, p: QPainter, rect: QRect, text: str,
                hot: bool, primary: bool) -> None:
        p.setPen(Qt.NoPen)
        if primary:
            p.setBrush(QColor("#3a9bff" if hot else ACCENT))
        else:
            p.setBrush(QColor("#3a3a3c" if hot else "#2c2c2e"))
        p.drawRoundedRect(rect, 9, 9)
        p.setFont(self._font(11))
        p.setPen(QColor("#ffffff" if primary or hot else "#d1d1d6"))
        p.drawText(rect, Qt.AlignCenter, text)
