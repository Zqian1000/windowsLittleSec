"""剪贴板历史：后台采集（只认文字）＋一个贴着岛弹出的列表。

采集时刻意跳过两类内容：
1. 密码管理器写入时会带 ``ExcludeClipboardContentFromMonitorProcessing``
   之类的格式，直接不记；
2. 我们自己写回剪贴板的那一下，用 ``_ignore`` 标记跳过，不然点一条记录
   会把它自己又顶到最前面。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QPointF
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QBrush, QPen
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
    QPushButton,
)

from core.clipstore import ClipItem, ClipStore

BG = "#1c1c1e"
TEXT = "#f2f2f7"
SUBTEXT = "#98989f"
ACCENT = "#0a84ff"

_BLOCK_FORMATS = (
    "ExcludeClipboardContentFromMonitorProcessing",
    "Clipboard Viewer Ignore",
    "CanIncludeInClipboardHistory",
)
_CLIP_VIEWER_IGNORE = 0x03  # 老式格式号，有些工具还在用


def set_plain_text(text: str) -> None:
    """只写 CF_UNICODETEXT，把网页/Word 带进来的 HTML、RTF 清干净。"""
    try:
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        k.GlobalAlloc.restype = ctypes.c_void_p
        k.GlobalLock.argtypes = [ctypes.c_void_p]
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]
        if not u.OpenClipboard(0):
            raise OSError("open clipboard")
        try:
            u.EmptyClipboard()
            data = (text + "\0").encode("utf-16-le")
            h = k.GlobalAlloc(0x0002, len(data))
            if not h:
                raise OSError("alloc")
            ptr = k.GlobalLock(h)
            if not ptr:
                raise OSError("lock")
            ctypes.memmove(ptr, data, len(data))
            k.GlobalUnlock(h)
            if not u.SetClipboardData(13, h):
                raise OSError("set")
        finally:
            u.CloseClipboard()
    except Exception:
        QGuiApplication.clipboard().setText(text)


def _clipboard_is_private() -> bool:
    """当前剪贴板内容是否被写入方声明成"不要采集"。"""
    u = ctypes.windll.user32
    if not u.OpenClipboard(0):
        return False
    try:
        blocked = False
        fmt = u.EnumClipboardFormats(0)
        while fmt:
            if fmt == _CLIP_VIEWER_IGNORE:
                blocked = True
            else:
                buf = ctypes.create_unicode_buffer(128)
                if u.GetClipboardFormatNameW(fmt, buf, 128):
                    name = buf.value
                    if name in _BLOCK_FORMATS:
                        # CanIncludeInClipboardHistory=0 才是"别记"
                        if name == "CanIncludeInClipboardHistory":
                            handle = u.GetClipboardData(fmt)
                            if handle:
                                ptr = ctypes.windll.kernel32.GlobalLock(handle)
                                if ptr:
                                    value = ctypes.cast(
                                        ptr, ctypes.POINTER(ctypes.c_uint32)).contents.value
                                    ctypes.windll.kernel32.GlobalUnlock(handle)
                                    blocked = blocked or value == 0
                        else:
                            blocked = True
            fmt = u.EnumClipboardFormats(fmt)
        return blocked
    except Exception:
        return False
    finally:
        try:
            u.CloseClipboard()
        except Exception:
            pass


class ClipboardWatcher(QWidget):
    """挂在 QApplication 的剪贴板信号上，收到文字就存一条。

    继承 QWidget 只是为了有个 parent 归属，本身不显示。
    """

    def __init__(self, store: ClipStore, enabled: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.enabled = enabled
        self._ignore = False
        self.hide()
        self._clip = QGuiApplication.clipboard()
        self._clip.dataChanged.connect(self._on_change)

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)

    def mark_self_write(self) -> None:
        """界面把某条记录写回剪贴板前调用，这一次变化不记。"""
        self._ignore = True

    def _on_change(self) -> None:
        if self._ignore:
            self._ignore = False
            return
        if not self.enabled:
            return
        try:
            if _clipboard_is_private():
                return
            text = self._clip.text()
        except Exception:
            return
        if not text or not text.strip():
            return
        self.store.add(text)


class ClipRow(QWidget):
    clicked = Signal(str)
    pin_clicked = Signal(str)
    toggle = Signal(str)

    def __init__(self, item: ClipItem, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self._hover = False
        self._select_mode = False
        self._selected = False
        self.setFixedHeight(54)
        self.setCursor(Qt.PointingHandCursor)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(10, 7, 8, 7)
        self.lay.setSpacing(8)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.text_label = QLabel(item.preview())
        self.text_label.setStyleSheet(f"color:{TEXT}; font-size:12px;")
        self.text_label.setWordWrap(True)
        self.meta = QLabel(("📌 固定 · " if item.pinned else "") + item.clock())
        self.meta.setStyleSheet(f"color:{SUBTEXT}; font-size:10px;")
        col.addWidget(self.text_label, 1)
        col.addWidget(self.meta)
        self.lay.addLayout(col, 1)

        self.pin = QPushButton("★" if item.pinned else "☆")
        self.pin.setFixedSize(22, 22)
        self.pin.setCursor(Qt.PointingHandCursor)
        self.pin.setToolTip("取消固定" if item.pinned else "固定这条")
        self.pin.setStyleSheet(
            "QPushButton{border:none;background:transparent;font-size:13px;"
            f"color:{ACCENT if item.pinned else SUBTEXT};}}"
            "QPushButton:hover{color:%s;}" % TEXT)
        self.pin.clicked.connect(lambda: self.pin_clicked.emit(self.item.key))
        self.lay.addWidget(self.pin, 0, Qt.AlignVCenter)

    def set_select_mode(self, on: bool) -> None:
        self._select_mode = bool(on)
        self.lay.setContentsMargins(26 if on else 10, 7, 8, 7)
        self.pin.setVisible(not on)
        self.update()

    def set_selected_state(self, on: bool) -> None:
        self._selected = bool(on)
        self.update()

    def enterEvent(self, _e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            if self._select_mode:
                self.toggle.emit(self.item.key)
            else:
                self.clicked.emit(self.item.key)
        super().mouseReleaseEvent(event)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if self._selected:
            p.setBrush(QBrush(QColor(35, 35, 38)))
        else:
            p.setBrush(QBrush(QColor(255, 255, 255, 16 if self._hover else 8)))
        p.drawRoundedRect(self.rect().adjusted(0, 1, 0, -1), 8, 8)
        if self._select_mode:
            r = 6.0
            cx, cy = 13, self.height() / 2
            p.setPen(QPen(QColor(ACCENT), 1.2))
            p.setBrush(QBrush(QColor(ACCENT)) if self._selected else Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r, r)
            if self._selected:
                p.setPen(QPen(QColor("#ffffff"), 1.4))
                p.drawLine(QPointF(cx - 2.5, cy), QPointF(cx - 0.5, cy + 2))
                p.drawLine(QPointF(cx - 0.5, cy + 2), QPointF(cx + 3, cy - 2.5))
        p.end()


class ClipboardPanel(QWidget):
    """从岛下方弹出的历史列表；点一条就写回剪贴板并收起。"""

    def __init__(self, store: ClipStore, watcher: Optional[ClipboardWatcher] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.watcher = watcher
        self._select_mode = False
        self._selected: set[str] = set()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(340)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        card = QWidget(self)
        card.setObjectName("card")
        card.setStyleSheet(f"#card{{background:{BG};border-radius:16px;}}")
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        head = QHBoxLayout()
        self.title = QLabel("剪贴板")
        self.title.setStyleSheet(f"color:{TEXT}; font-size:13px; font-weight:600;")
        head.addWidget(self.title)
        head.addStretch(1)
        self.btn_select = QPushButton("多选")
        self.btn_select.setCursor(Qt.PointingHandCursor)
        self.btn_select.setStyleSheet(
            "QPushButton{border:none;background:transparent;font-size:11px;"
            f"color:{SUBTEXT};}} QPushButton:hover{{color:{TEXT};}}")
        self.btn_select.clicked.connect(self._toggle_select_mode)
        head.addWidget(self.btn_select)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.setStyleSheet(
            "QPushButton{border:none;background:transparent;font-size:11px;"
            f"color:{SUBTEXT};}} QPushButton:hover{{color:{TEXT};}}")
        self.btn_clear.clicked.connect(self._clear)
        head.addWidget(self.btn_clear)
        lay.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea{background:transparent;}"
            "QScrollBar:vertical{width:6px;background:transparent;}"
            "QScrollBar::handle:vertical{background:#3a3a3c;border-radius:3px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
        self.body = QWidget()
        self.body.setStyleSheet("background:transparent;")
        self.rows = QVBoxLayout(self.body)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        self.rows.addStretch(1)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll, 1)

        self.footer = QWidget()
        foot = QHBoxLayout(self.footer)
        foot.setContentsMargins(0, 2, 0, 0)
        foot.setSpacing(6)
        self.btn_merge = QPushButton("合并复制")
        self.btn_merge.setCursor(Qt.PointingHandCursor)
        self.btn_merge.setFixedHeight(28)
        self.btn_merge.setStyleSheet(
            "QPushButton{border:none;border-radius:7px;background:#2c2c2e;"
            f"color:{TEXT};font-size:12px;padding:0 12px;}}"
            "QPushButton:hover{background:#3a3a3c;}"
            "QPushButton:disabled{color:#5a5a5e;background:#242426;}")
        self.btn_merge.clicked.connect(self._merge)
        foot.addWidget(self.btn_merge)
        foot.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.setFixedHeight(28)
        self.btn_cancel.setStyleSheet(
            "QPushButton{border:none;border-radius:7px;background:transparent;"
            f"color:{SUBTEXT};font-size:12px;padding:0 10px;}}"
            f"QPushButton:hover{{color:{TEXT};}}")
        self.btn_cancel.clicked.connect(self._toggle_select_mode)
        foot.addWidget(self.btn_cancel)
        self.footer.setVisible(False)
        lay.addWidget(self.footer)

    # ---- 展示 ----
    def refresh(self) -> None:
        while self.rows.count():
            it = self.rows.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif it.layout():
                it.layout().deleteLater()
        items = self.store.items()
        live = {it.key for it in items}
        self._selected &= live
        suffix = f" · 已选 {len(self._selected)}" if self._select_mode else ""
        self.title.setText(f"剪贴板 · {len(items)} 条{suffix}")
        self.btn_clear.setVisible(len(items) > 0 and not self._select_mode)
        self.btn_select.setVisible(len(items) > 0)
        self.btn_select.setText("取消多选" if self._select_mode else "多选")
        self.footer.setVisible(self._select_mode)
        self.btn_merge.setEnabled(bool(self._selected))
        self.btn_merge.setText(f"合并复制 ({len(self._selected)})" if self._selected
                               else "合并复制")
        if not items:
            tip = QLabel("还没有复制过文字")
            tip.setAlignment(Qt.AlignCenter)
            tip.setStyleSheet(f"color:{SUBTEXT}; font-size:12px; padding:26px 0;")
            self.rows.addWidget(tip)
        for item in items:
            row = ClipRow(item, self.body)
            row.set_select_mode(self._select_mode)
            row.set_selected_state(item.key in self._selected)
            row.pin_clicked.connect(self._toggle_pin)
            if self._select_mode:
                row.toggle.connect(self._toggle_select)
            else:
                row.clicked.connect(self._copy)
            self.rows.addWidget(row)
        self.rows.addStretch(1)
        # 关掉 widgetResizable 后要自己给 body 尺寸：宽度跟视口走，高度按行数撑开，
        # 否则固定高度的行会被压在一起叠着画。
        total = 54 * len(items) + 2 * max(0, len(items) - 1) + 4
        self.body.setFixedWidth(300)      # 给右侧滚动条留 8px
        self.body.setFixedHeight(max(total, 1))
        self._fit_height(len(items))

    def _fit_height(self, n: int) -> None:
        content_h = max(54 * min(n, 7), 60) + 60
        extra = 38 if self._select_mode else 0
        self.setFixedHeight(min(468 if self._select_mode else 430, content_h + extra))

    def popup_near(self, anchor) -> None:
        """贴在岛的下方；贴边时自动往回收，别跑出屏幕。"""
        self.refresh()
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = anchor.center().x() - self.width() // 2
        y = anchor.bottom() + 8
        x = max(geo.left() + 6, min(x, geo.right() - self.width() - 6))
        if y + self.height() > geo.bottom():
            y = max(geo.top() + 6, anchor.top() - self.height() - 8)
        self.move(x, y)
        self.show()
        self.body.setFixedWidth(300)
        self.raise_()
        self.activateWindow()

    # ---- 操作 ----
    def _toggle_select_mode(self) -> None:
        self._select_mode = not self._select_mode
        self._selected.clear()
        self.refresh()

    def _toggle_select(self, key: str) -> None:
        if key in self._selected:
            self._selected.discard(key)
        else:
            self._selected.add(key)
        self.refresh()

    def _merge(self) -> None:
        parts = [it.text for it in self.store.items() if it.key in self._selected]
        if not parts:
            return
        merged = "\n".join(parts)
        if self.watcher is not None:
            self.watcher.mark_self_write()
        set_plain_text(merged)
        self.store.add(merged)
        self._select_mode = False
        self._selected.clear()
        self.hide()

    def _copy(self, key: str) -> None:
        item = self.store.get(key)
        if item is None:
            return
        if self.watcher is not None:
            self.watcher.mark_self_write()
        set_plain_text(item.text)           # 只留纯文本，不带网页/Word 的格式
        self.store.add(item.text)          # 时间顶到最新，下次还在这儿能看到
        self.hide()

    def _toggle_pin(self, key: str) -> None:
        self.store.toggle_pin(key)
        self.refresh()

    def _clear(self) -> None:
        self.store.clear_unpinned()
        self.refresh()

    # ---- 事件 ----
    def hideEvent(self, event) -> None:
        # 点到别处自动收起时，顺手退出多选，别让下次打开还停在勾选态
        if self._select_mode:
            self._select_mode = False
            self._selected.clear()
        super().hideEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _e) -> None:
        # 圆角卡片由内部 card 画，这里只负责把阴影区域留空
        pass
