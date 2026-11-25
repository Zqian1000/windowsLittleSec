"""可钉住的快捷键速查浮层：一张卡片 + 搜索框 + 分组列表。

数据与界面分开，后面要加 Word / VS Code 的速查表时只换数据，不用改面板。
"""
from __future__ import annotations

from typing import Iterable, Sequence
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QBrush
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

BG = "#1c1c1e"
CARD = "#242426"
TEXT = "#f2f2f7"
SUBTEXT = "#98989f"
ACCENT = "#0a84ff"
KEY = "#ffd60a"


def _chip_style(active: bool) -> str:
    if active:
        return ("QPushButton{border:none;border-radius:8px;background:#0a84ff;"
                "color:#ffffff;font-size:11px;padding:4px 12px;font-weight:600;}")
    return ("QPushButton{border:none;border-radius:8px;background:#2c2c2e;"
            "color:#d1d1d6;font-size:11px;padding:4px 12px;}"
            "QPushButton:hover{background:#3a3a3c;color:#ffffff;}")


# (分组名, [(按键, 功能), ...])——Windows 版 Excel，含少量经典菜单键
EXCEL_SHORTCUTS: Sequence[tuple[str, Sequence[tuple[str, str]]]] = (
    ("文件与工作簿", (
        ("Ctrl+N", "新建工作簿"),
        ("Ctrl+O", "打开文件"),
        ("Ctrl+S", "保存"),
        ("F12", "另存为"),
        ("Ctrl+W", "关闭当前工作簿"),
        ("Ctrl+P", "打印"),
        ("Ctrl+Tab", "在打开的工作簿之间切换"),
    )),
    ("光标与导航", (
        ("方向键", "上下左右移动一个单元格"),
        ("Ctrl+方向键", "跳到当前数据区域的边缘"),
        ("Ctrl+Home", "回到 A1"),
        ("Ctrl+End", "跳到已用区域的右下角"),
        ("PageUp / PageDown", "向上 / 向下翻一屏"),
        ("Alt+PageUp / PageDown", "向左 / 向右翻一屏"),
        ("Ctrl+G（或 F5）", "定位：按名称或地址跳转"),
        ("Tab / Shift+Tab", "向右 / 向左移动"),
        ("Enter / Shift+Enter", "向下 / 向上移动"),
    )),
    ("选择区域", (
        ("Shift+方向键", "逐格扩展选择"),
        ("Ctrl+Shift+方向键", "一路扩到数据区域边缘"),
        ("Ctrl+Shift+Home", "扩到 A1"),
        ("Ctrl+Shift+End", "扩到已用区域末尾"),
        ("Ctrl+Space", "选中整列"),
        ("Shift+Space", "选中整行"),
        ("Ctrl+A", "选中当前区域，再按一次全选"),
        ("Ctrl+Shift+*", "选中当前数据区域"),
        ("Shift+F8", "打开“扩展选择”模式，方向键继续扩"),
    )),
    ("输入与编辑", (
        ("F2", "编辑当前单元格"),
        ("Alt+Enter", "单元格内换行"),
        ("Ctrl+Enter", "把输入内容填进整个选中区域"),
        ("Ctrl+D", "向下填充（复制上方内容）"),
        ("Ctrl+R", "向右填充（复制左侧内容）"),
        ("Ctrl+;", "插入当天日期"),
        ("Ctrl+Shift+;", "插入当前时间"),
        ("Ctrl+'", "复制上一行的公式"),
        ("Ctrl+Alt+V", "选择性粘贴"),
        ("Esc", "取消输入 / 关闭菜单"),
        ("Delete", "清除单元格内容"),
        ("Ctrl+Z / Ctrl+Y", "撤销 / 重做"),
        ("Ctrl+C / X / V", "复制 / 剪切 / 粘贴"),
    )),
    ("公式与计算", (
        ("=", "开始输入公式"),
        ("F4", "切换绝对/相对引用（$A$1 → A$1 → $A1 → A1）"),
        ("Ctrl+`", "显示 / 隐藏所有公式"),
        ("Shift+F3", "插入函数对话框"),
        ("Alt+=", "自动求和"),
        ("F9", "重算所有工作簿（Shift+F9 只算当前表）"),
    )),
    ("格式与样式", (
        ("Ctrl+1", "打开“设置单元格格式”"),
        ("Ctrl+B / I / U", "加粗 / 斜体 / 下划线"),
        ("Ctrl+5", "加删除线"),
        ("Ctrl+Shift+&", "给选中区域加外边框"),
        ("Ctrl+Shift+_", "去掉边框"),
        ("Ctrl+Shift+~", "常规格式"),
        ("Ctrl+Shift+1", "数字：两位小数 + 千分位"),
        ("Ctrl+Shift+2", "时间格式"),
        ("Ctrl+Shift+3", "日期格式"),
        ("Ctrl+Shift+4", "货币格式"),
        ("Ctrl+Shift+5", "百分比格式"),
        ("Ctrl+Shift+6", "科学计数"),
        ("Alt+H,O,I", "自动调整列宽"),
        ("Alt+H,O,A", "自动调整行高"),
    )),
    ("行列与结构", (
        ("Ctrl+加号 / 减号", "插入 / 删除单元格"),
        ("Ctrl+9", "隐藏选中行（Ctrl+Shift+9 取消隐藏）"),
        ("Ctrl+0", "隐藏选中列（Ctrl+Shift+0 取消隐藏）"),
        ("Ctrl+Shift+加号", "在选中区域插入空白单元格"),
    )),
    ("工作表与视图", (
        ("Shift+F11", "新建工作表"),
        ("Ctrl+PageDown / PageUp", "切到下一张 / 上一张工作表"),
        ("Alt+W,F,F", "冻结窗格"),
        ("Ctrl+F1", "显示 / 隐藏功能区"),
        ("Ctrl+Shift+L", "打开 / 关闭筛选"),
        ("Ctrl+T（或 Ctrl+L）", "把区域变成表格"),
        ("Alt+F1", "在当前位置插入图表"),
        ("F11", "新建一张图表工作表"),
    )),
    ("查找与其他", (
        ("Ctrl+F", "查找"),
        ("Ctrl+H", "替换"),
        ("Ctrl+K", "插入超链接"),
        ("Shift+F10", "打开右键菜单"),
        ("Ctrl+Shift+F1", "全屏显示"),
    )),
)


class CheatSheetPanel(QWidget):
    """贴着岛弹出的速查卡；「钉住」后点别的窗口也不会自己收起。"""

    def __init__(self, title: str, groups: Iterable[tuple[str, Sequence[tuple[str, str]]]],
                 width: int = 380, parent=None) -> None:
        super().__init__(parent)
        self.pinned = False
        self._drag_pos: QPoint | None = None
        self._groups: list[tuple[QLabel, list[tuple[QWidget, str, str]]]] = []

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(width)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        card = QWidget(self)
        card.setObjectName("card")
        card.setStyleSheet(f"#card{{background:{BG};border-radius:16px;}}")
        outer.addWidget(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(8)

        # 顶栏：标题 + 钉住 + 关闭（按住这一行可以拖走卡片）
        self.header = QWidget(card)
        head = QHBoxLayout(self.header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.title = QLabel(title)
        self.title.setStyleSheet(f"color:{TEXT}; font-size:14px; font-weight:600;")
        head.addWidget(self.title)
        head.addStretch(1)
        self.btn_pin = QPushButton("钉住")
        self.btn_pin.setCursor(Qt.PointingHandCursor)
        self.btn_pin.setCheckable(True)
        self.btn_pin.setToolTip("钉住后点别的窗口也不会自动收起；再点一下取消")
        self.btn_pin.setStyleSheet(_chip_style(False))
        self.btn_pin.toggled.connect(self.set_pinned)
        head.addWidget(self.btn_pin)
        self.btn_close = QPushButton("✕")
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setFixedSize(22, 22)
        self.btn_close.setToolTip("关闭（Esc 也行）")
        self.btn_close.setStyleSheet(
            "QPushButton{border:none;background:transparent;color:#98989f;"
            "font-size:12px;}QPushButton:hover{color:#ffffff;}")
        self.btn_close.clicked.connect(self.hide)
        head.addWidget(self.btn_close)
        box.addWidget(self.header)
        self.header.installEventFilter(self)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索按键或功能，比如“冻结”“Ctrl+1”")
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(
            f"QLineEdit{{background:{CARD};color:{TEXT};border:none;border-radius:8px;"
            "padding:6px 9px;font-size:12px;}}")
        self.search.textChanged.connect(self._apply_filter)
        box.addWidget(self.search)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.scroll.viewport().setStyleSheet("background:transparent;")
        self.scroll.setFixedHeight(470)
        box.addWidget(self.scroll)

        self._content = QWidget()
        self._content.setStyleSheet("background:transparent;")
        self._cl = QVBoxLayout(self._content)
        self._cl.setContentsMargins(0, 0, 6, 0)
        self._cl.setSpacing(4)
        self.scroll.setWidget(self._content)

        for name, items in groups:
            self._add_group(name, items)
        self._cl.addStretch(1)
        self._natural = self.sizeHint().height()
        # 除滚动区以外固定占的高度（标题、搜索框、边距），贴边时按它算剩余空间
        self._overhead = self._natural - self.scroll.height()

    # ---- 内容 ----
    def _add_group(self, name: str, items: Sequence[tuple[str, str]]) -> None:
        head = QLabel(name)
        head.setStyleSheet(
            f"color:{ACCENT};font-size:12px;font-weight:600;padding-top:6px;")
        self._cl.addWidget(head)
        rows: list[tuple[QWidget, str, str]] = []
        for keys, desc in items:
            row = QWidget(self._content)
            row.setStyleSheet("background:transparent;")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(10)
            k = QLabel(keys)
            k.setFixedWidth(142)
            k.setWordWrap(True)
            k.setStyleSheet(f"color:{KEY};font-size:11px;font-weight:600;")
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setStyleSheet(f"color:{SUBTEXT};font-size:11px;")
            lay.addWidget(k)
            lay.addWidget(d, 1)
            self._cl.addWidget(row)
            rows.append((row, keys.lower(), desc.lower()))
        self._groups.append((head, rows))

    def _apply_filter(self, text: str) -> None:
        q = (text or "").strip().lower()
        for head, rows in self._groups:
            any_visible = False
            for row, keys, desc in rows:
                show = (not q) or (q in keys) or (q in desc)
                row.setVisible(show)
                any_visible = any_visible or show
            head.setVisible(any_visible)

    # ---- 钉住 ----
    def set_pinned(self, on: bool) -> None:
        self.pinned = bool(on)
        self.btn_pin.setText("已钉住" if self.pinned else "钉住")
        self.btn_pin.setStyleSheet(_chip_style(self.pinned))

    # ---- 位置 ----
    def popup_near(self, anchor) -> None:
        """贴在岛下方；下面放不下就翻到上面，两边都放不下就压矮滚动区。"""
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        nat = self._natural
        below = geo.bottom() - 10 - (anchor.bottom() + 8)
        above = anchor.top() - 8 - (geo.top() + 6)

        if nat <= below:
            h, y = nat, anchor.bottom() + 8
        elif nat <= above:
            h, y = nat, anchor.top() - nat - 8
        else:
            h = max(240, min(nat, max(below, above)))
            if below >= above:
                y = anchor.bottom() + 8
            else:
                y = max(geo.top() + 6, anchor.top() - h - 8)
        self.scroll.setFixedHeight(max(110, h - self._overhead))
        self.setFixedHeight(h)

        x = anchor.center().x() - self.width() // 2
        x = max(geo.left() + 6, min(x, geo.right() - self.width() - 6))
        self.move(x, y)
        self.show()
        self.raise_()

    # ---- 事件 ----
    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.header:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == QEvent.MouseMove and self._drag_pos is not None:
                if event.buttons() & Qt.LeftButton:
                    self.move(event.globalPosition().toPoint() - self._drag_pos)
                    return True
            if event.type() == QEvent.MouseButtonRelease:
                self._drag_pos = None
                return True
        return super().eventFilter(obj, event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor("transparent")))
        p.setPen(Qt.NoPen)
        p.end()
