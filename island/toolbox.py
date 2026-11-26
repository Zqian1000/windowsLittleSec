"""工具箱：选哪些工具按钮出现在胶囊上。

每个工具一行：勾选框、图标、名称和一句说明。勾上/取消立即生效并落盘；
面板开着的时候胶囊不会自动收起，点「完成」或 Esc 才恢复靠边隐藏。

实现上刻意不用 QLabel / QPushButton / QCheckBox：PySide 在本机首次显示这些
控件时，样式与字体初始化要 1.5～3 秒，点工具箱就会明显卡一下。整块面板自绘，
命中区域用矩形判断，工具箱和胶囊共用 `widgets.draw_glyph` 的同一套图标。
"""
from __future__ import annotations

from typing import Iterable, Sequence

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from .widgets import draw_glyph
from .popup_layout import place_popup

BG = "#1c1c1e"
TEXT = "#f2f2f7"
SUBTEXT = "#98989f"
ACCENT = "#0a84ff"

WIDTH = 420
PAD = 16
TITLE_H = 22
HINT_H = 18
CHECK_TOP = 62
ROW_H = 34
FOOT_H = 28

# (key, 名称, 说明, 图标)
TOOLS: Sequence[tuple[str, str, str, str]] = (
    ("clip", "剪贴板历史", "复制过的文字，点一条写回剪贴板", "clip"),
    ("snip", "区域截图", "框选屏幕区域，Ctrl+Alt+X 也能触发", "snip"),
    ("bell", "新建提醒", "到点弹内容泡，可配截图", "bell"),
    ("list", "提醒列表", "查看、完成、删除已有提醒", "list"),
    ("fav", "网站收藏", "收藏常用网站，点一下用默认浏览器打开", "globe"),
    ("bejson", "Bejson", "用浏览器打开 JSON 格式化工具站", "json"),
    ("excel", "Excel 快捷键", "速查卡，可钉在屏幕上对照操作", "excel"),
    ("proj", "应用快开", "直接列出开始菜单应用，点一下启动；也能切到项目目录", "apps"),
    ("incident", "报错现场包", "窗口、进程和剪贴板报错拼成 Markdown", "alert"),
    ("clerk", "文员工具", "金额大写、日期、重命名、清单、文本整理", "doc"),
)
ICONS = {key: icon for key, _name, _desc, icon in TOOLS}
DEFAULT_TOOLS = ["clip", "snip", "bell", "list", "fav", "bejson", "excel"]
ALL_TOOLS = [k for k, _n, _d, _i in TOOLS]
HEIGHT = CHECK_TOP + len(TOOLS) * ROW_H + FOOT_H + PAD + 8


class ToolboxPanel(QWidget):
    """工具勾选面板，改一项就发一次 changed（保持顺序稳定）。"""

    changed = Signal(list)
    closed = Signal()

    def __init__(self, selected: Iterable[str] = (), parent=None) -> None:
        super().__init__(parent)
        self._selected = set(selected) & set(ALL_TOOLS)
        self._hot = ""
        self._row_hot = -1

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(WIDTH, HEIGHT)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        # 工具箱要点「完成」才关；点别处不收（PopupGuard 看这个标记）
        self.auto_dismiss = False

        self._close_rect = QRect(WIDTH - PAD - 22, PAD - 2, 22, 22)
        fy = HEIGHT - PAD - FOOT_H
        self._all_rect = QRect(PAD, fy, 52, FOOT_H)
        self._default_rect = QRect(PAD + 60, fy, 76, FOOT_H)
        self._done_rect = QRect(WIDTH - PAD - 64, fy, 64, FOOT_H)

    # ---- 状态 ----
    def selected(self) -> list[str]:
        return [k for k, _n, _d, _i in TOOLS if k in self._selected]

    def set_selected(self, keys: Iterable[str]) -> None:
        want = set(keys) & set(ALL_TOOLS)
        changed = want != self._selected
        self._selected = want
        self.update()
        if changed:
            self.changed.emit(self.selected())

    def _toggle(self, key: str) -> None:
        if key in self._selected:
            self._selected.remove(key)
        else:
            self._selected.add(key)
        self.update()
        self.changed.emit(self.selected())

    # ---- 位置/关闭 ----
    def popup_near(self, anchor: QRect) -> None:
        place_popup(self, anchor)
        self.show()
        self.raise_()
        self.activateWindow()

    def close_panel(self) -> None:
        if not self.isVisible():
            return
        self.hide()
        self.closed.emit()

    # ---- 命中区域 ----
    def _row_rect(self, index: int) -> QRect:
        return QRect(PAD, CHECK_TOP + index * ROW_H,
                     WIDTH - PAD * 2, ROW_H - 2)

    def _row_at(self, pos) -> int:
        if pos.x() < PAD or pos.x() > WIDTH - PAD:
            return -1
        if pos.y() < CHECK_TOP:
            return -1
        index = (pos.y() - CHECK_TOP) // ROW_H
        if 0 <= index < len(TOOLS):
            return index
        return -1

    def _hit(self, pos) -> str:
        if self._close_rect.contains(pos):
            return "close"
        if self._all_rect.contains(pos):
            return "all"
        if self._default_rect.contains(pos):
            return "default"
        if self._done_rect.contains(pos):
            return "done"
        return ""

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        hot = self._hit(pos)
        row = -1 if hot else self._row_at(pos)
        if hot != self._hot or row != self._row_hot:
            self._hot = hot
            self._row_hot = row
            self.setCursor(Qt.PointingHandCursor if (hot or row >= 0) else Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hot or self._row_hot >= 0:
            self._hot = ""
            self._row_hot = -1
            self.setCursor(Qt.ArrowCursor)
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            hit = self._hit(pos)
            if hit == "close" or hit == "done":
                self.close_panel()
                return
            if hit == "all":
                self.set_selected(ALL_TOOLS)
                return
            if hit == "default":
                self.set_selected(DEFAULT_TOOLS)
                return
            row = self._row_at(pos)
            if row >= 0:
                self._toggle(TOOLS[row][0])
                return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close_panel()
            return
        super().keyPressEvent(event)

    def event(self, e) -> bool:
        # 工具箱要等用户点「完成」，失焦不自己收
        if e.type() == QEvent.WindowDeactivate:
            self.raise_()
        return super().event(e)

    # ---- 绘制 ----
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(BG))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 16, 16)

        # 标题
        f = QFont(self.font())
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(TEXT))
        p.drawText(QRect(PAD, PAD - 4, 200, TITLE_H), Qt.AlignLeft | Qt.AlignVCenter, "工具箱")

        # 右上角关闭
        p.setFont(self._font(13))
        p.setPen(QColor("#ffffff" if self._hot == "close" else SUBTEXT))
        p.drawText(self._close_rect, Qt.AlignCenter, "✕")

        # 副标题
        p.setFont(self._font(11))
        p.setPen(QColor(SUBTEXT))
        p.drawText(QRect(PAD, CHECK_TOP - HINT_H - 8, WIDTH - PAD * 2, HINT_H),
                   Qt.AlignLeft | Qt.AlignVCenter, "勾选后立即生效；取消只是不显示，随时能加回来。")

        # 9 行工具：勾选框 + 图标 + 名称 + 说明
        name_font = self._font(12)
        desc_font = self._font(10)
        for i, (key, name, desc, icon) in enumerate(TOOLS):
            rect = self._row_rect(i)
            selected = key in self._selected
            if self._row_hot == i:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 16))
                p.drawRoundedRect(rect, 8, 8)

            # 勾选框
            box = QRectF(rect.x() + 2, rect.center().y() - 8, 16, 16)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT if selected else "#2c2c2e"))
            p.drawRoundedRect(box, 5, 5)
            if not selected:
                p.setPen(QColor("#3a3a3c"))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(box, 5, 5)
            else:
                check = QRectF(box.x() + 4, box.y() + 7, 4, 4)
                p.setPen(QColor("#ffffff"))
                p.setBrush(Qt.NoBrush)
                p.drawLine(check.topLeft(), QPointF(check.right(), check.bottom()))
                p.drawLine(QPointF(check.right(), check.bottom()),
                           QPointF(box.right() - 4, box.top() + 5))

            # 图标
            icon_rect = QRectF(rect.x() + 26, rect.center().y() - 9, 18, 18)
            draw_glyph(p, icon, icon_rect,
                       QColor(ACCENT if selected else "#c7c7cc"))

            # 名称 + 说明
            name_x = rect.x() + 52
            p.setFont(name_font)
            p.setPen(QColor(TEXT))
            name_w = QFontMetrics(name_font).horizontalAdvance(name)
            p.drawText(QRect(name_x, rect.y(), name_w + 2, rect.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, name)

            avail = rect.right() - 6 - (name_x + name_w + 8)
            if avail > 20:
                p.setFont(desc_font)
                p.setPen(QColor(SUBTEXT))
                fm = QFontMetrics(desc_font)
                p.drawText(QRect(name_x + name_w + 8, rect.y(), avail, rect.height()),
                           Qt.AlignLeft | Qt.AlignVCenter,
                           fm.elidedText("·  " + desc, Qt.ElideRight, avail))

        # 底部按钮
        fy = HEIGHT - PAD - FOOT_H
        self._draw_ghost(p, self._all_rect, "全选", self._hot == "all")
        self._draw_ghost(p, self._default_rect, "恢复默认", self._hot == "default")
        self._draw_primary(p, self._done_rect, "完成", self._hot == "done")
        p.end()

    def _font(self, px: int) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(px)
        return f

    def _draw_ghost(self, p: QPainter, rect: QRect, text: str, hot: bool) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#3a3a3c" if hot else "#2c2c2e"))
        p.drawRoundedRect(rect, 8, 8)
        p.setFont(self._font(11))
        p.setPen(QColor("#ffffff" if hot else "#d1d1d6"))
        p.drawText(rect, Qt.AlignCenter, text)

    def _draw_primary(self, p: QPainter, rect: QRect, text: str, hot: bool) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#3a9bff" if hot else ACCENT))
        p.drawRoundedRect(rect, 9, 9)
        f = self._font(12)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(rect, Qt.AlignCenter, text)
