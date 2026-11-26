"""剪贴板历史：后台采集文字和图片＋一个贴着岛弹出的列表。

对齐 Windows 自带剪贴板（Win+V）的几个关键点：
- 文字、图片都能记；图片在列表里显示缩略图，点一下写回剪贴板；
- 固定项排在最上面；支持搜索、单条删除、清空未固定；
- 键盘可用：↑/↓ 选、回车粘贴、Delete 删除、Esc 收起。

采集时刻意跳过两类内容：
1. 密码管理器写入时会带 ``ExcludeClipboardContentFromMonitorProcessing``
   之类的格式，直接不记；
2. 我们自己写回剪贴板的那一下，用 ``_ignore`` 标记跳过，不然点一条记录
   会把它自己又顶到最前面。
"""
from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QPointF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QGuiApplication, QImage, QImageReader, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout,
    QWidget, QPushButton,
)

from core.clipstore import ClipItem, ClipStore
from .popup_layout import place_popup

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

BTN_FLAT = ("QPushButton{border:none;background:transparent;font-size:11px;"
            "color:%s;}QPushButton:hover{color:#ffffff;}" % SUBTEXT)


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


def set_image(path: str) -> bool:
    """把历史里的图片副本写回剪贴板；写失败返回 False。"""
    try:
        img = QImage(path)
        if img.isNull():
            return False
        QGuiApplication.clipboard().setImage(img)
        return True
    except Exception:
        return False


def _clipboard_is_private() -> bool:
    """当前剪贴板内容是否被写入方声明成"不要采集"。"""
    if not hasattr(ctypes, "windll"):        # 非 Windows（离屏测试）直接放行
        return False
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    # 64 位下句柄是 8 字节，不声明 restype 会被截成 int32
    u.GetClipboardData.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
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
                                ptr = k.GlobalLock(handle)
                                if ptr:
                                    value = ctypes.cast(
                                        ptr, ctypes.POINTER(ctypes.c_uint32)).contents.value
                                    k.GlobalUnlock(handle)
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


def _thumb_pixmap(path: str, side: int) -> QImage:
    """按缩略尺寸解码，别把 4K 原图整张读进内存再缩。"""
    try:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.width() > 0 and size.height() > 0:
            scale = min(side / size.width(), side / size.height(), 1.0)
            reader.setScaledSize(QSize(max(1, int(size.width() * scale)),
                                       max(1, int(size.height() * scale))))
        return reader.read()
    except Exception:
        return QImage()


class ClipboardWatcher(QWidget):
    """挂在 QApplication 的剪贴板信号上，收到文字/图片就存一条。

    继承 QWidget 只是为了有个 parent 归属，本身不显示。
    """

    def __init__(self, store: ClipStore, enabled: bool = True,
                 images: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.enabled = enabled
        self.images = images
        self._ignore = False
        self.hide()
        self._clip = QGuiApplication.clipboard()
        self._clip.dataChanged.connect(self._on_change)

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)

    def mark_self_write(self) -> None:
        """界面把某条记录写回剪贴板前调用，这一次变化不记。"""
        self._ignore = True

    def cancel_self_write(self) -> None:
        """写回失败时撤掉标记，别把用户下一次正常复制吞掉。"""
        self._ignore = False

    def _on_change(self) -> None:
        if self._ignore:
            self._ignore = False
            return
        if not self.enabled:
            return
        try:
            if _clipboard_is_private():
                return
            mime = self._clip.mimeData()
            text = self._clip.text() if mime is not None and mime.hasText() else ""
            if text and text.strip():
                # 文字优先：Excel 选区、网页富文本都会同时带一张位图，
                # 用户真正想留的是可粘贴的文字。
                self.store.add(text)
                return
            if not self.images or mime is None or not mime.hasImage():
                return
            img = self._clip.image()
            if img.isNull() or img.width() < 1 or img.height() < 1:
                return
            buf = QBuffer()
            buf.open(QIODevice.WriteOnly)
            ok = img.save(buf, "PNG")
            data = bytes(buf.data()) if ok else b""
            buf.close()
            if data:
                self.store.add_image_bytes(data, img.width(), img.height())
        except Exception:
            return


class ClipRow(QWidget):
    clicked = Signal(str)
    pin_clicked = Signal(str)
    remove_clicked = Signal(str)
    toggle = Signal(str)

    def __init__(self, item: ClipItem, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self._hover = False
        self._select_mode = False
        self._selected = False
        self._current = False
        self.setFixedHeight(68 if item.is_image else 54)
        self.setCursor(Qt.PointingHandCursor)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(10, 7, 8, 7)
        self.lay.setSpacing(8)

        if item.is_image:
            thumb = QLabel()
            thumb.setFixedSize(52, 52)
            thumb.setAlignment(Qt.AlignCenter)
            thumb.setStyleSheet("QLabel{background:#2c2c2e;border-radius:8px;}")
            img = _thumb_pixmap(item.image, 50)
            if not img.isNull():
                thumb.setPixmap(QPixmap.fromImage(img))
            else:
                thumb.setText("🖼")
                thumb.setStyleSheet(
                    "QLabel{background:#2c2c2e;border-radius:8px;color:#98989f;}")
            thumb.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.lay.addWidget(thumb)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        shown = item.text if item.is_image and item.text else item.preview()
        self.text_label = QLabel(shown)
        self.text_label.setStyleSheet(f"color:{TEXT}; font-size:12px;")
        self.text_label.setWordWrap(True)
        meta = QLabel(("📌 固定 · " if item.pinned else "") + item.clock())
        meta.setStyleSheet(f"color:{SUBTEXT}; font-size:10px;")
        col.addWidget(self.text_label, 1)
        col.addWidget(meta)
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

        self.rm = QPushButton("×")
        self.rm.setFixedSize(18, 22)
        self.rm.setCursor(Qt.PointingHandCursor)
        self.rm.setToolTip("删除这条（Delete）")
        self.rm.setStyleSheet(
            "QPushButton{border:none;background:transparent;color:#5a5a5e;"
            "font-size:13px;}QPushButton:hover{color:#ff453a;}")
        self.rm.clicked.connect(lambda: self.remove_clicked.emit(self.item.key))
        self.lay.addWidget(self.rm, 0, Qt.AlignVCenter)

    def set_select_mode(self, on: bool) -> None:
        self._select_mode = bool(on)
        self.lay.setContentsMargins(26 if on else 10, 7, 8, 7)
        self.pin.setVisible(not on)
        self.rm.setVisible(not on)
        self.update()

    def set_selected_state(self, on: bool) -> None:
        self._selected = bool(on)
        self.update()

    def set_current(self, on: bool) -> None:
        self._current = bool(on)
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
        if self._current and not self._select_mode:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(ACCENT), 1.2))
            p.drawRoundedRect(self.rect().adjusted(1, 2, -1, -2), 8, 8)
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
        self._query = ""
        self._items: list[ClipItem] = []
        self._rows: list[ClipRow] = []
        self._cursor = -1
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedWidth(370)

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
        self.btn_select.setStyleSheet(BTN_FLAT)
        self.btn_select.clicked.connect(self._toggle_select_mode)
        head.addWidget(self.btn_select)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.setStyleSheet(BTN_FLAT)
        self.btn_clear.setToolTip("只清未固定的记录")
        self.btn_clear.clicked.connect(self._clear)
        head.addWidget(self.btn_clear)
        lay.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索剪贴板…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(32)
        self.search.setStyleSheet(
            f"QLineEdit{{background:#2c2c2e;color:{TEXT};border:none;"
            "border-radius:8px;padding:4px 9px;font-size:12px;}"
            "QLineEdit::placeholder{color:#6e6e73;}")
        self.search.textChanged.connect(self._on_search)
        self.search.installEventFilter(self)
        lay.addWidget(self.search)

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
    def _on_search(self, text: str) -> None:
        self._query = (text or "").strip().lower()
        self._cursor = 0 if self._query else -1
        self.refresh()

    def _visible_items(self) -> list[ClipItem]:
        items = self.store.items()
        if not self._query:
            return items
        out = []
        for item in items:
            hay = (item.preview(6, 200) + " " + ("图片" if item.is_image else "")).lower()
            if self._query in hay:
                out.append(item)
        return out

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
        items = self._visible_items()
        self._items = items
        self._rows = []
        live = {it.key for it in items}
        self._selected &= live
        suffix = f" · 已选 {len(self._selected)}" if self._select_mode else ""
        title_total = self.store.count()
        self.title.setText(f"剪贴板 · {len(items)}/{title_total} 条{suffix}"
                           if self._query else f"剪贴板 · {title_total} 条{suffix}")
        self.btn_clear.setVisible(title_total > 0 and not self._select_mode)
        self.btn_select.setVisible(title_total > 0)
        self.btn_select.setText("取消多选" if self._select_mode else "多选")
        self.footer.setVisible(self._select_mode)
        merge_n = sum(1 for it in items
                      if it.key in self._selected and not it.is_image and it.text)
        self.btn_merge.setEnabled(merge_n > 0)
        self.btn_merge.setText(f"合并复制 ({merge_n})" if merge_n else "合并复制")

        heads = 0
        if items:
            pinned = [i for i in items if i.pinned]
            recent = [i for i in items if not i.pinned]
            if pinned:
                self._add_section("已固定")
                heads += 1
                for item in pinned:
                    self._add_row(item)
            if recent:
                if pinned:
                    self._add_section("最近")
                    heads += 1
                for item in recent:
                    self._add_row(item)
        else:
            tip = QLabel("没有匹配的记录" if self._query else "还没有复制过文字或图片")
            tip.setAlignment(Qt.AlignCenter)
            tip.setStyleSheet(f"color:{SUBTEXT}; font-size:12px; padding:26px 0;")
            self.rows.addWidget(tip)
            self._rows = []
        self.rows.addStretch(1)

        if self._cursor >= len(self._rows):
            self._cursor = len(self._rows) - 1
        self._apply_cursor()

        row_h = sum(r.height() for r in self._rows)
        total = row_h + 2 * max(0, len(self._rows) - 1) + heads * 22
        self.body.setFixedWidth(330)      # 给右侧滚动条留 8px
        self.body.setFixedHeight(max(total, 1))
        self._fit_height(total)

    def _add_section(self, text: str) -> None:
        lab = QLabel(text)
        lab.setFixedHeight(20)
        lab.setStyleSheet(f"color:{SUBTEXT}; font-size:10px; padding-left:4px;")
        self.rows.addWidget(lab)

    def _add_row(self, item: ClipItem) -> None:
        row = ClipRow(item, self.body)
        row.set_select_mode(self._select_mode)
        row.set_selected_state(item.key in self._selected)
        row.pin_clicked.connect(self._toggle_pin)
        row.remove_clicked.connect(self._remove)
        if self._select_mode:
            row.toggle.connect(self._toggle_select)
        else:
            row.clicked.connect(self._copy)
        self.rows.addWidget(row)
        self._rows.append(row)

    def _fit_height(self, content_h: int) -> None:
        chrome = 98 if self._query else 98
        extra = 38 if self._select_mode else 0
        self.setFixedHeight(min(520, max(180, content_h + chrome + extra)))

    def popup_near(self, anchor) -> None:
        """贴在岛的下方；位置统一交给 PopupLayout 避让。"""
        self.refresh()
        place_popup(self, anchor)
        self.show()
        self.body.setFixedWidth(330)
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.search.setFocus()

    # ---- 键盘 ----
    def eventFilter(self, obj, event) -> bool:
        if obj is self.search and event.type() == QEvent.KeyPress:
            # 搜索框里还有字时，Delete 是删字符，不能拿去删剪贴板条目
            if event.key() == Qt.Key_Delete and self.search.text():
                return super().eventFilter(obj, event)
            if self._handle_key(event):
                return True
        return super().eventFilter(obj, event)

    def _handle_key(self, event) -> bool:
        key = event.key()
        if key == Qt.Key_Down:
            self._move_cursor(1)
            return True
        if key == Qt.Key_Up:
            self._move_cursor(-1)
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._activate_current()
            return True
        if key == Qt.Key_Delete:
            self._remove_current()
            return True
        if key == Qt.Key_Escape:
            self.hide()
            return True
        if key == Qt.Key_PageDown:
            self._move_cursor(7)
            return True
        if key == Qt.Key_PageUp:
            self._move_cursor(-7)
            return True
        return False

    def keyPressEvent(self, event) -> None:
        if self._handle_key(event):
            return
        super().keyPressEvent(event)

    def _move_cursor(self, delta: int) -> None:
        if not self._rows:
            return
        if self._cursor < 0:
            self._cursor = 0
        else:
            self._cursor = max(0, min(len(self._rows) - 1, self._cursor + delta))
        self._apply_cursor()
        row = self._rows[self._cursor]
        self.scroll.ensureWidgetVisible(row, 0, 18)
        self.setFocus()

    def _apply_cursor(self) -> None:
        for idx, row in enumerate(self._rows):
            row.set_current(idx == self._cursor)

    def _current_item(self) -> Optional[ClipItem]:
        if 0 <= self._cursor < len(self._rows):
            return self._rows[self._cursor].item
        return None

    def _activate_current(self) -> None:
        item = self._current_item()
        if item is None and self._rows:
            item = self._rows[0].item
        if item is None:
            return
        if self._select_mode:
            self._toggle_select(item.key)
        else:
            self._copy(item.key)

    def _remove_current(self) -> None:
        item = self._current_item()
        if item is None:
            return
        self.store.remove(item.key)
        self.refresh()

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
        parts = [it.text for it in self.store.items()
                 if it.key in self._selected and not it.is_image and it.text]
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
        if item.is_image:
            if not set_image(item.image):
                if self.watcher is not None:
                    self.watcher.cancel_self_write()
                return
        else:
            set_plain_text(item.text)       # 只留纯文本，不带网页/Word 的格式
        self.store.touch(item.key)          # 时间顶到最新，下次还在这儿能看到
        self.hide()

    def _toggle_pin(self, key: str) -> None:
        self.store.toggle_pin(key)
        self.refresh()

    def _remove(self, key: str) -> None:
        self.store.remove(key)
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
        self._cursor = -1
        if self._query:
            self._query = ""
            self.search.clear()
        super().hideEvent(event)

    def paintEvent(self, _e) -> None:
        # 圆角卡片由内部 card 画，这里只负责把阴影区域留空
        pass
