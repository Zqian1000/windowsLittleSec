"""从屏幕右侧滑出的提醒列表（To Do 式任务流）。

和原来的下拉列表分开：这里是一整块右侧抽屉，支持快速添加、分组、
勾选完成、任务标签、打开详情改标题/时间，不再从胶囊下面往下长。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import (
    QDateTime, QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QGuiApplication, QKeySequence, QPainter, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup, QDateTimeEdit, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from .image_viewer import show_image
from .popup_layout import PopupLayout
from core import recurrence
from core.reminders import Reminder, ReminderStore

BG = "#1b1b1d"
CARD = "#262629"
CARD_HOVER = "#303034"
TEXT = "#f2f2f7"
SUBTEXT = "#98989f"
FAINT = "#636366"
ACCENT = "#0a84ff"
GREEN = "#30d158"
ORANGE = "#ff9f0a"
GOLD = "#ffd60a"
RED = "#ff453a"
BLUE = "#64d2ff"

WIDTH = 420
MARGIN = 12

# 任务标签：颜色和文案集中在这里；数据层只存 normal/urgent/important/later。
PRIORITY_META = {
    "normal": ("无标签", SUBTEXT, "transparent"),
    "urgent": ("紧急", RED, "#3a2224"),
    "important": ("重要", GOLD, "#3a3520"),
    "later": ("可延后", BLUE, "#1f3038"),
}
PRIORITY_OPTIONS = (
    ("urgent", "紧急"),
    ("important", "重要"),
    ("later", "可延后"),
    ("normal", "无标签"),
)

FILTERS = (
    ("all", "任务"),
    ("today", "今天"),
    ("urgent", "紧急"),
    ("important", "重要"),
    ("later", "可延后"),
    ("done", "已完成"),
)

GROUP_TITLES = ("已过期", "今天", "明天", "之后", "已完成")


def _due_label(item: Reminder) -> str:
    dt = datetime.fromtimestamp(item.due)
    today = date.today()
    if item.done:
        return f"已完成 · {dt:%m-%d %H:%M}"
    if item.due <= time.time():
        return f"已过期 · {dt:%m-%d %H:%M}"
    if dt.date() == today:
        return f"今天 {dt:%H:%M}"
    if dt.date() == today + timedelta(days=1):
        return f"明天 {dt:%H:%M}"
    if 0 < (dt.date() - today).days < 7:
        week = "一二三四五六日"[dt.weekday()]
        return f"周{week} {dt:%H:%M}"
    return f"{dt:%m-%d %H:%M}"


def _bucket(item: Reminder) -> int:
    if item.done:
        return 4
    if item.due <= time.time():
        return 0
    d = datetime.fromtimestamp(item.due).date()
    today = date.today()
    if d == today:
        return 1
    if d == today + timedelta(days=1):
        return 2
    return 3


class _CheckButton(QPushButton):
    """To Do 的圆圈勾选：未选是空心圆，选中是蓝底白勾。"""

    def __init__(self, checked: bool, parent=None) -> None:
        super().__init__("✓", parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(24, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton{border:1px solid #6c6c70;border-radius:12px;"
            "background:transparent;color:transparent;font-size:13px;}"
            "QPushButton:hover{border-color:#98989f;}"
            "QPushButton:checked{background:%s;border-color:%s;color:#fff;}"
            % (ACCENT, ACCENT))


class _TaskRow(QWidget):
    """一条任务：勾选、标题、时间、截图缩略、任务标签。"""

    done_changed = Signal(str, bool)
    priority_changed = Signal(str, str)
    opened = Signal(str)

    def __init__(self, item: Reminder, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self._hover = False
        self.setFixedHeight(66 if item.image and Path(item.image).exists() else 58)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点开详情")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 7, 6, 7)
        lay.setSpacing(9)

        self.check = _CheckButton(item.done, self)
        self.check.clicked.connect(
            lambda checked: self.done_changed.emit(self.item.id, checked))
        lay.addWidget(self.check)

        if item.image and Path(item.image).exists():
            thumb = QLabel(self)
            thumb.setFixedSize(40, 40)
            thumb.setAlignment(Qt.AlignCenter)
            pix = QPixmap(item.image)
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(40, 40, Qt.KeepAspectRatio,
                                           Qt.SmoothTransformation))
            thumb.setStyleSheet("QLabel{background:#2c2c2e;border-radius:7px;}")
            thumb.setCursor(Qt.PointingHandCursor)
            thumb.setToolTip("点一下看大图")
            thumb.mousePressEvent = lambda _e, p=item.image: show_image(p)
            lay.addWidget(thumb)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.title = QLabel(self)
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.RichText)
        title_html = escape(item.text or "看图提醒")
        repeat = item.repeat_text()
        if repeat:
            title_html += (
                f' <span style="color:{BLUE};font-size:10px;">'
                f'· {escape(repeat)}</span>')
        self.title.setText(title_html)
        self.title.setStyleSheet(
            f"QLabel{{color:{SUBTEXT if item.done else TEXT};font-size:13px;}}"
            + ("QLabel{text-decoration:line-through;}" if item.done else ""))
        self.meta = QLabel(_due_label(item), self)
        self.meta.setStyleSheet(
            f"QLabel{{color:{ORANGE if item.overdue else SUBTEXT};font-size:10px;}}")
        col.addWidget(self.title, 1)
        col.addWidget(self.meta)
        lay.addLayout(col, 1)

        self.tag = QPushButton(self)
        self.tag.setFixedSize(58, 24)
        self.tag.setCursor(Qt.PointingHandCursor)
        self.tag.setToolTip("点击设置任务标签")
        self.tag.clicked.connect(self._show_tag_menu)
        self._style_tag(item.priority)
        lay.addWidget(self.tag, 0, Qt.AlignVCenter)

    def _style_tag(self, key: str) -> None:
        label, fg, bg = PRIORITY_META.get(key, PRIORITY_META["normal"])
        text = "＋标签" if key == "normal" else label
        border = "#3a3a3c" if key == "normal" else fg
        self.tag.setText(text)
        self.tag.setStyleSheet(
            f"QPushButton{{border:1px solid {border};border-radius:7px;"
            f"background:{bg};color:{fg};font-size:10px;padding:0 4px;}}"
            "QPushButton:hover{background:#3a3a3c;color:#fff;}")

    def _show_tag_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#262629;color:#f2f2f7;border:1px solid #3a3a3c;"
            "padding:4px;border-radius:6px;}"
            "QMenu::item{padding:5px 18px 5px 10px;border-radius:4px;}"
            "QMenu::item:selected{background:#3a3a3c;}")
        selected = {"key": ""}

        def choose(key: str) -> None:
            selected["key"] = key
            menu.close()

        for key, label in PRIORITY_OPTIONS:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, k=key: choose(k))
        menu.exec(self.tag.mapToGlobal(QPoint(0, self.tag.height() + 4)))
        # 等菜单关闭后再发信号；收到信号后列表会重建行，不能把正在用的菜单父窗口删掉。
        if selected["key"]:
            self.priority_changed.emit(self.item.id, selected["key"])

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.opened.emit(self.item.id)
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(CARD_HOVER if self._hover else CARD)))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect().adjusted(0, 1, 0, -1), 9, 9)
        p.end()


class ReminderDrawer(QWidget):
    """右侧整高抽屉，替代原来自胶囊下方的 ReminderListPanel。"""

    def __init__(self, store: ReminderStore,
                 on_changed: Optional[Callable[[], None]] = None,
                 on_new: Optional[Callable[[], None]] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self._on_changed = on_changed
        self._on_new = on_new
        self._filter = "all"
        self._detail_id = ""
        self._add_priority = "normal"
        self._add_recurrence = recurrence.NONE
        self._anim: Optional[QPropertyAnimation] = None
        self._closing = False

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("提醒")
        self.setFixedWidth(WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        card = QWidget(self)
        card.setObjectName("drawerCard")
        card.setStyleSheet(
            f"#drawerCard{{background:{BG};border:1px solid #303033;"
            "border-radius:16px;}}")
        outer.addWidget(card)
        body = QVBoxLayout(card)
        body.setContentsMargins(18, 16, 18, 16)
        body.setSpacing(12)

        self._build_header(body)
        self._build_filters(body)
        self.stack = QStackedWidget(card)
        self.stack.setStyleSheet("QStackedWidget{background:transparent;}")
        self.stack.addWidget(self._build_list_page())
        self.stack.addWidget(self._build_detail_page())
        body.addWidget(self.stack, 1)
        self.stack.setCurrentIndex(0)
        self._filter_buttons["all"].setChecked(True)

        QShortcut(QKeySequence("Escape"), self, activated=self._escape)
        self.refresh()

    # ---- 结构 ---------------------------------------------------------
    def _build_header(self, body: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(1)
        title = QLabel("提醒")
        title.setStyleSheet(f"color:{TEXT};font-size:21px;font-weight:700;")
        col.addWidget(title)
        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet(f"color:{SUBTEXT};font-size:11px;")
        col.addWidget(self.subtitle)
        row.addLayout(col, 1)

        close = QPushButton("✕")
        close.setFixedSize(28, 28)
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip("收起")
        close.setStyleSheet(
            f"QPushButton{{border:none;border-radius:14px;background:transparent;"
            f"color:{SUBTEXT};font-size:14px;}}"
            "QPushButton:hover{background:#2c2c2e;color:#fff;}")
        close.clicked.connect(self.close_drawer)
        row.addWidget(close, 0, Qt.AlignTop)
        body.addLayout(row)

    def _build_filters(self, body: QVBoxLayout) -> None:
        self.filters_holder = QWidget()
        self.filters_holder.setStyleSheet("background:transparent;")
        row = QHBoxLayout(self.filters_holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self._filter_buttons = {}
        for key, label in FILTERS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{border:none;border-radius:11px;padding:5px 10px;"
                "background:transparent;color:%s;font-size:12px;}"
                "QPushButton:hover{background:#2c2c2e;color:#fff;}"
                "QPushButton:checked{background:#2c2c2e;color:#fff;}"
                % SUBTEXT)
            btn.clicked.connect(lambda _checked=False, k=key: self._set_filter(k))
            self._filter_group.addButton(btn)
            self._filter_buttons[key] = btn
            row.addWidget(btn)
        row.addStretch(1)
        body.addWidget(self.filters_holder)

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        add_box = QWidget(page)
        add_box.setStyleSheet(
            f"QWidget{{background:{CARD};border-radius:11px;}}")
        add_lay = QVBoxLayout(add_box)
        add_lay.setContentsMargins(10, 8, 10, 8)
        add_lay.setSpacing(7)

        input_row = QHBoxLayout()
        input_row.setSpacing(7)
        self.input = QLineEdit(add_box)
        self.input.setPlaceholderText("添加任务，按回车保存")
        self.input.setClearButtonEnabled(True)
        self.input.setStyleSheet(
            "QLineEdit{border:none;background:transparent;color:%s;"
            "font-size:13px;padding:2px 0;}"
            "QLineEdit::placeholder{color:%s;}" % (TEXT, FAINT))
        self.input.returnPressed.connect(self._quick_add)
        input_row.addWidget(self.input, 1)
        self.add_tag = QPushButton(add_box)
        self.add_tag.setFixedSize(58, 28)
        self.add_tag.setCursor(Qt.PointingHandCursor)
        self.add_tag.setToolTip("为这条新任务选标签")
        self.add_tag.clicked.connect(self._pick_add_priority)
        self._style_add_tag()
        input_row.addWidget(self.add_tag)
        self.btn_add = QPushButton("添加", add_box)
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.setFixedHeight(28)
        self.btn_add.setStyleSheet(
            "QPushButton{border:none;border-radius:8px;background:%s;"
            "color:#fff;font-size:12px;padding:0 12px;}"
            "QPushButton:hover{background:#3a9bff;}" % ACCENT)
        self.btn_add.clicked.connect(self._quick_add)
        input_row.addWidget(self.btn_add)
        add_lay.addLayout(input_row)

        when_row = QHBoxLayout()
        when_row.setSpacing(7)
        tip = QLabel("提醒时间", add_box)
        tip.setStyleSheet(f"color:{SUBTEXT};font-size:10px;")
        when_row.addWidget(tip)
        self.when = QDateTimeEdit(add_box)
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("MM-dd HH:mm")
        self.when.setDateTime(QDateTime.currentDateTime().addSecs(1800))
        self.when.setFixedHeight(26)
        self.when.setFixedWidth(136)
        self.when.setStyleSheet(
            "QDateTimeEdit{background:#1f1f22;color:%s;border:none;"
            "border-radius:7px;padding:2px 6px;font-size:11px;}"
            "QDateTimeEdit::drop-down{border:none;width:14px;}" % TEXT)
        when_row.addWidget(self.when)
        self.add_recur = QPushButton(add_box)
        self.add_recur.setFixedSize(58, 26)
        self.add_recur.setCursor(Qt.PointingHandCursor)
        self.add_recur.clicked.connect(self._pick_add_recurrence)
        self._style_add_recurrence()
        when_row.addWidget(self.add_recur)
        hint = QLabel("到点会弹窗", add_box)
        hint.setStyleSheet(f"color:{FAINT};font-size:10px;")
        when_row.addWidget(hint)
        when_row.addStretch(1)
        if self._on_new is not None:
            shot = QPushButton("截图提醒", add_box)
            shot.setCursor(Qt.PointingHandCursor)
            shot.setToolTip("打开原来的新建窗口，可以给提醒附一张截图")
            shot.setStyleSheet(
                f"QPushButton{{border:none;background:transparent;color:{SUBTEXT};"
                "font-size:10px;padding:2px 0;}"
                "QPushButton:hover{color:#fff;}")
            shot.clicked.connect(self._on_new)
            when_row.addWidget(shot)
        add_lay.addLayout(when_row)
        lay.addWidget(add_box)

        self.scroll = QScrollArea(page)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea{background:transparent;}"
            "QScrollBar:vertical{width:6px;background:transparent;}"
            "QScrollBar::handle:vertical{background:#3a3a3c;border-radius:3px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
        self.body_w = QWidget()
        self.body_w.setStyleSheet("background:transparent;")
        self.rows = QVBoxLayout(self.body_w)
        self.rows.setContentsMargins(0, 0, 2, 0)
        self.rows.setSpacing(4)
        self.scroll.setWidget(self.body_w)
        lay.addWidget(self.scroll, 1)
        return page

    def _build_detail_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        back = QPushButton("‹  所有任务")
        back.setCursor(Qt.PointingHandCursor)
        back.setStyleSheet(
            f"QPushButton{{border:none;background:transparent;color:{SUBTEXT};"
            "font-size:12px;padding:2px 0;text-align:left;}"
            "QPushButton:hover{color:#fff;}")
        back.clicked.connect(self._back)
        lay.addWidget(back)

        head = QLabel("任务详情")
        head.setStyleSheet(f"color:{TEXT};font-size:17px;font-weight:600;")
        lay.addWidget(head)

        field_tip = QLabel("标题")
        field_tip.setStyleSheet(f"color:{SUBTEXT};font-size:10px;")
        lay.addWidget(field_tip)
        self.detail_title = QLineEdit()
        self.detail_title.setStyleSheet(
            f"QLineEdit{{background:{CARD};color:{TEXT};border:none;"
            "border-radius:9px;padding:9px 10px;font-size:14px;}")
        self.detail_title.editingFinished.connect(self._save_detail_title)
        lay.addWidget(self.detail_title)

        time_tip = QLabel("提醒时间")
        time_tip.setStyleSheet(f"color:{SUBTEXT};font-size:10px;")
        lay.addWidget(time_tip)
        self.detail_when = QDateTimeEdit()
        self.detail_when.setCalendarPopup(True)
        self.detail_when.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.detail_when.setStyleSheet(
            f"QDateTimeEdit{{background:{CARD};color:{TEXT};border:none;"
            "border-radius:9px;padding:8px 10px;font-size:12px;}"
            "QDateTimeEdit::drop-down{border:none;width:18px;}")
        self.detail_when.dateTimeChanged.connect(self._save_detail_due)
        lay.addWidget(self.detail_when)

        tag_tip = QLabel("标签")
        tag_tip.setStyleSheet(f"color:{SUBTEXT};font-size:10px;")
        lay.addWidget(tag_tip)
        self.priority_group = QButtonGroup(self)
        self.priority_group.setExclusive(True)
        self.priority_buttons = {}
        tag_row = QHBoxLayout()
        tag_row.setSpacing(6)
        for key, label in PRIORITY_OPTIONS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(28)
            _, fg, bg = PRIORITY_META[key]
            check_bg = bg if key != "normal" else "#3a3a3c"
            check_fg = fg if key != "normal" else TEXT
            btn.setStyleSheet(
                f"QPushButton{{border:1px solid #3a3a3c;border-radius:8px;"
                f"background:{CARD};color:{SUBTEXT};font-size:11px;"
                "padding:0 9px;}"
                "QPushButton:hover{border-color:#6c6c70;color:#fff;}"
                f"QPushButton:checked{{background:{check_bg};color:{check_fg};"
                f"border-color:{check_fg};}}")
            btn.clicked.connect(
                lambda _checked=False, k=key: self._set_detail_priority(k))
            self.priority_group.addButton(btn)
            self.priority_buttons[key] = btn
            tag_row.addWidget(btn)
        tag_row.addStretch(1)
        lay.addLayout(tag_row)

        self.detail_state = QLabel("")
        self.detail_state.setStyleSheet(f"color:{ORANGE};font-size:11px;")
        lay.addWidget(self.detail_state)

        self.detail_image = QLabel()
        self.detail_image.setAlignment(Qt.AlignCenter)
        self.detail_image.setStyleSheet(
            f"QLabel{{background:{CARD};border-radius:10px;}}")
        self.detail_image.setCursor(Qt.PointingHandCursor)
        self.detail_image.setToolTip("点一下看大图")
        self.detail_image.mousePressEvent = lambda _e: self._open_detail_image()
        self.detail_image.hide()
        lay.addWidget(self.detail_image)

        lay.addStretch(1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_detail_done = QPushButton("完成")
        self.btn_detail_done.setCursor(Qt.PointingHandCursor)
        self.btn_detail_done.setStyleSheet(
            "QPushButton{border:none;border-radius:9px;background:%s;"
            "color:#fff;font-size:12px;padding:8px 18px;}"
            "QPushButton:hover{background:#3a9bff;}" % ACCENT)
        self.btn_detail_done.clicked.connect(self._toggle_detail_done)
        actions.addWidget(self.btn_detail_done)
        actions.addStretch(1)
        self.btn_delete = QPushButton("删除")
        self.btn_delete.setCursor(Qt.PointingHandCursor)
        self.btn_delete.setStyleSheet(
            f"QPushButton{{border:none;border-radius:9px;background:transparent;"
            f"color:{SUBTEXT};font-size:12px;padding:8px 12px;}}"
            "QPushButton:hover{color:#ff453a;background:#2c2c2e;}")
        self.btn_delete.clicked.connect(self._delete_detail)
        actions.addWidget(self.btn_delete)
        lay.addLayout(actions)
        return page

    # ---- 数据与列表 ---------------------------------------------------
    def set_filter(self, key: str) -> None:
        self._set_filter(key)

    def _set_filter(self, key: str) -> None:
        if key not in self._filter_buttons:
            return
        self._filter = key
        self._filter_buttons[key].setChecked(True)
        self._back()
        self.refresh()

    def _pending(self) -> list[Reminder]:
        return [i for i in self.store.items() if not i.done]

    def _visible_items(self) -> list[Reminder]:
        items = self.store.items()
        if self._filter == "done":
            return [i for i in items if i.done]
        pending = [i for i in items if not i.done]
        if self._filter == "today":
            return [i for i in pending if _bucket(i) in (0, 1)]
        if self._filter == "urgent":
            return [i for i in pending if i.priority == "urgent"]
        if self._filter == "important":
            return [i for i in pending if i.priority == "important"]
        if self._filter == "later":
            return [i for i in pending if i.priority == "later"]
        return pending

    def refresh(self) -> None:
        while self.rows.count():
            it = self.rows.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        items = self._visible_items()
        pending_all = self._pending()
        overdue = sum(1 for i in pending_all if i.overdue)
        self.subtitle.setText(
            f"{len(pending_all)} 条待办" + (f" · {overdue} 条已过期" if overdue else "")
            if pending_all else "暂无待办")
        self._filter_buttons["all"].setText("任务")
        self._filter_buttons["today"].setText("今天")
        self._filter_buttons["urgent"].setText("紧急")
        self._filter_buttons["important"].setText("重要")
        self._filter_buttons["later"].setText("可延后")
        self._filter_buttons["done"].setText(
            f"已完成 {sum(1 for i in self.store.items() if i.done)}"
            if any(i.done for i in self.store.items()) else "已完成")

        if not items:
            tip = QLabel(
                "还没有任务，在上面输入一条" if not pending_all
                else "这个列表是空的")
            tip.setAlignment(Qt.AlignCenter)
            tip.setStyleSheet(f"color:{SUBTEXT};font-size:12px;padding:36px 0;")
            self.rows.addWidget(tip)
        else:
            last = -1
            for item in items:
                b = _bucket(item)
                if b != last:
                    cap = QLabel(GROUP_TITLES[b])
                    cap.setStyleSheet(
                        f"QLabel{{color:{SUBTEXT};font-size:11px;font-weight:600;"
                        "padding:6px 4px 2px 4px;}")
                    self.rows.addWidget(cap)
                    last = b
                row = _TaskRow(item, self.body_w)
                row.done_changed.connect(self._toggle_done)
                row.priority_changed.connect(self._set_priority)
                row.opened.connect(self._open_detail)
                self.rows.addWidget(row)
        self.rows.addStretch(1)
        self.body_w.adjustSize()
        self.body_w.setMinimumHeight(max(120, self.rows.sizeHint().height()))

    # ---- 快速添加 -----------------------------------------------------
    def _style_add_tag(self) -> None:
        label, fg, bg = PRIORITY_META.get(
            self._add_priority, PRIORITY_META["normal"])
        text = "＋标签" if self._add_priority == "normal" else label
        border = "#3a3a3c" if self._add_priority == "normal" else fg
        self.add_tag.setText(text)
        self.add_tag.setStyleSheet(
            f"QPushButton{{border:1px solid {border};border-radius:8px;"
            f"background:{bg};color:{fg};font-size:10px;padding:0 4px;}}"
            "QPushButton:hover{background:#3a3a3c;color:#fff;}")

    def _style_add_recurrence(self) -> None:
        label = recurrence.label(self._add_recurrence)
        active = self._add_recurrence != recurrence.NONE
        text = label if active else "＋周期"
        fg = BLUE if active else SUBTEXT
        border = BLUE if active else "#3a3a3c"
        bg = "#1f3038" if active else "transparent"
        self.add_recur.setText(text)
        self.add_recur.setStyleSheet(
            f"QPushButton{{border:1px solid {border};border-radius:8px;"
            f"background:{bg};color:{fg};font-size:10px;padding:0 4px;}}"
            "QPushButton:hover{background:#3a3a3c;color:#fff;}")

    def _pick_add_recurrence(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#262629;color:#f2f2f7;border:1px solid #3a3a3c;"
            "padding:4px;border-radius:6px;}"
            "QMenu::item{padding:5px 18px 5px 10px;border-radius:4px;}"
            "QMenu::item:selected{background:#3a3a3c;}")
        selected = {"key": ""}

        def choose(key: str) -> None:
            selected["key"] = key
            menu.close()

        for key in recurrence.RULES:
            action = menu.addAction(recurrence.label(key))
            action.triggered.connect(
                lambda _checked=False, k=key: choose(k))
        menu.exec(self.add_recur.mapToGlobal(
            QPoint(0, self.add_recur.height() + 4)))
        if selected["key"]:
            self._set_add_recurrence(selected["key"])

    def _set_add_recurrence(self, key: str) -> None:
        self._add_recurrence = recurrence.normalize(key)
        self._style_add_recurrence()

    def _pick_add_priority(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#262629;color:#f2f2f7;border:1px solid #3a3a3c;"
            "padding:4px;border-radius:6px;}"
            "QMenu::item{padding:5px 18px 5px 10px;border-radius:4px;}"
            "QMenu::item:selected{background:#3a3a3c;}")
        for key, label in PRIORITY_OPTIONS:
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, k=key: self._set_add_priority(k))
        menu.exec(self.add_tag.mapToGlobal(QPoint(0, self.add_tag.height() + 4)))

    def _set_add_priority(self, key: str) -> None:
        self._add_priority = key
        self._style_add_tag()

    def _quick_add(self) -> None:
        text = self.input.text().strip()
        if not text:
            self.input.setFocus()
            return
        due = self.when.dateTime().toPython().timestamp()
        self.store.add(text, due, priority=self._add_priority,
                       recurrence_rule=self._add_recurrence)
        self.input.clear()
        self.when.setDateTime(QDateTime.currentDateTime().addSecs(1800))
        self._add_priority = "normal"
        self._add_recurrence = recurrence.NONE
        self._style_add_tag()
        self._style_add_recurrence()
        self.refresh()
        self._changed()
        self.input.setFocus()

    def focus_add(self) -> None:
        self.input.setFocus()

    # ---- 行操作 -------------------------------------------------------
    def _toggle_done(self, rid: str, done: bool) -> None:
        self.store.set_done(rid, done)
        self.refresh()
        self._changed()

    def _set_priority(self, rid: str, priority: str) -> None:
        self.store.set_priority(rid, priority)
        self.refresh()
        self._changed()

    def _changed(self) -> None:
        if self._on_changed is not None:
            self._on_changed()

    # ---- 详情 ---------------------------------------------------------
    def _open_detail(self, rid: str) -> None:
        item = self.store.get(rid)
        if item is None:
            return
        self._detail_id = rid
        self.detail_title.setText(item.text or "看图提醒")
        self.detail_when.blockSignals(True)
        self.detail_when.setDateTime(
            QDateTime.fromSecsSinceEpoch(int(item.due)))
        self.detail_when.blockSignals(False)
        btn = self.priority_buttons.get(item.priority)
        if btn is not None:
            btn.setChecked(True)
        self.btn_detail_done.setText("重新打开" if item.done else "完成")
        self.detail_state.setText("已完成" if item.done else _due_label(item))
        self.detail_state.setStyleSheet(
            f"color:{GREEN if item.done else (ORANGE if item.overdue else SUBTEXT)};"
            "font-size:11px;")
        if item.image and Path(item.image).exists():
            pix = QPixmap(item.image)
            if not pix.isNull():
                self.detail_image.setPixmap(
                    pix.scaled(360, 220, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation))
                self.detail_image.show()
            else:
                self.detail_image.hide()
        else:
            self.detail_image.hide()
        self.stack.setCurrentIndex(1)
        self.filters_holder.hide()
        self.detail_title.setFocus()

    def _save_detail_title(self) -> None:
        if not self._detail_id:
            return
        item = self.store.get(self._detail_id)
        if item is None:
            return
        text = self.detail_title.text().strip()
        if not text:
            self.detail_title.setText(item.text or "看图提醒")
            return
        if text != item.text:
            self.store.update(self._detail_id, text=text)
            self.refresh()
            self._changed()

    def _save_detail_due(self, _dt: QDateTime) -> None:
        if not self._detail_id:
            return
        due = self.detail_when.dateTime().toPython().timestamp()
        item = self.store.get(self._detail_id)
        if item is None or abs(due - item.due) < 1:
            return
        self.store.update(self._detail_id, due=due)
        self._refresh_detail_state()
        self.refresh()
        self._changed()

    def _refresh_detail_state(self) -> None:
        item = self.store.get(self._detail_id)
        if item is None:
            return
        self.detail_state.setText("已完成" if item.done else _due_label(item))
        self.detail_state.setStyleSheet(
            f"color:{GREEN if item.done else (ORANGE if item.overdue else SUBTEXT)};"
            "font-size:11px;")

    def _set_detail_priority(self, key: str) -> None:
        if not self._detail_id:
            return
        self.store.set_priority(self._detail_id, key)
        btn = self.priority_buttons.get(key)
        if btn is not None:
            btn.setChecked(True)
        self.refresh()
        self._changed()

    def _toggle_detail_done(self) -> None:
        if not self._detail_id:
            return
        item = self.store.get(self._detail_id)
        if item is None:
            return
        self.store.set_done(self._detail_id, not item.done)
        self._open_detail(self._detail_id)
        self.refresh()
        self._changed()

    def _delete_detail(self) -> None:
        if not self._detail_id:
            return
        self.store.remove(self._detail_id)
        self._detail_id = ""
        self.stack.setCurrentIndex(0)
        self.filters_holder.show()
        self.refresh()
        self._changed()

    def _open_detail_image(self) -> None:
        item = self.store.get(self._detail_id)
        if item is not None and item.image:
            show_image(item.image)

    def _back(self) -> None:
        self._detail_id = ""
        self.stack.setCurrentIndex(0)
        self.filters_holder.show()

    def _escape(self) -> None:
        if self.stack.currentIndex() == 1:
            self._back()
        else:
            self.close_drawer()

    # ---- 显示与动画 ---------------------------------------------------
    def popup_near(self, anchor: Optional[QRect] = None) -> None:
        """贴着屏幕右侧出现；anchor 只用来判断在哪个显示器。"""
        screen = None
        if anchor is not None:
            screen = QGuiApplication.screenAt(anchor.center())
        screen = screen or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        h = max(520, geo.height() - 2 * MARGIN)
        self.setFixedSize(WIDTH, h)
        # 右侧抽屉保留自己的贴边动画，但登记进公共浮层表：
        # 其它卡片摆放时会把它当作不可遮挡区域。
        PopupLayout.shared().register(self)
        target = QPoint(geo.right() - WIDTH - MARGIN + 1, geo.top() + MARGIN)
        self._closing = False
        if self._anim is not None:
            self._anim.stop()
        if self.isVisible():
            self.move(target)
            self.raise_()
            return
        self._back()
        self.refresh()
        start = QPoint(geo.right() + 4, target.y())
        self.move(start)
        self.show()
        self.raise_()
        self.activateWindow()
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(240)
        self._anim.setStartValue(start)
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()

    def close_drawer(self) -> None:
        if not self.isVisible():
            return
        geo = self.geometry()
        self._closing = True
        if self._anim is not None:
            self._anim.stop()
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(180)
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(QPoint(geo.right() + 8, self.y()))
        self._anim.setEasingCurve(QEasingCurve.InCubic)
        self._anim.finished.connect(self._finish_close)
        self._anim.start()

    def _finish_close(self) -> None:
        if self._closing:
            self._closing = False
            self.hide()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._escape()
            return
        super().keyPressEvent(event)
