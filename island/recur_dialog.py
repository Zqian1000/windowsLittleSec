"""带周期选项的提醒新建卡。

不直接改原来的 `ReminderDialog`：右侧 To Do 抽屉还在迭代，新建窗口保持原样，
这里只加一层子类，把「重复」下拉插在时间下面。以后要加别的提醒字段也只在
这个子类里动，不会牵连原有流程。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QListView

from core import recurrence
from .reminders_ui import (ACCENT, CARD, SUBTEXT, TEXT, ReminderDialog,
                           _copy_image)


class RecurringReminderDialog(ReminderDialog):
    """原新建窗口 + 「重复」：不重复 / 每天 / 工作日 / 每周 / 每月。"""

    def __init__(self, store, snip=None, parent=None) -> None:
        super().__init__(store, snip=snip, parent=parent)
        row = QHBoxLayout()
        row.setSpacing(8)
        lab = QLabel("重复")
        lab.setStyleSheet(f"color:{SUBTEXT}; font-size:11px;")
        row.addWidget(lab)
        self.repeat = QComboBox()
        self.repeat.setStyleSheet(
            f"QComboBox{{background:{CARD};color:{TEXT};border:none;"
            "border-radius:8px;padding:4px 8px;font-size:12px;}"
            "QComboBox::drop-down{border:none;width:16px;}")
        # 深色主题下原生弹出列表会继承系统配色，出现“黑底黑字”。
        # 自建 view 明确写死背景/文字/选中态，保证每一项都看得清。
        popup = QListView(self.repeat)
        popup.setStyleSheet(
            f"QListView{{background:#323234;color:{TEXT};"
            "border:1px solid #4a4a4c;border-radius:8px;outline:0;"
            "padding:4px;font-size:12px;}"
            "QListView::item{min-height:26px;padding:2px 8px;"
            "border-radius:5px;}"
            f"QListView::item:selected{{background:{ACCENT};color:#ffffff;}}"
            "QListView::item:hover{background:#3f3f42;}")
        popup.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.repeat.setView(popup)
        for rule in recurrence.RULES:
            self.repeat.addItem(recurrence.label(rule), rule)
        row.addWidget(self.repeat, 1)
        # 0 标题 / 1 内容 / 2 时间 / 3 截图，插在截图前面
        self.body.insertLayout(3, row)
        self.adjustSize()

    def open_new(self, image_path: str = "") -> None:
        super().open_new(image_path)
        self.repeat.setCurrentIndex(0)

    def _save(self) -> None:
        text = self.text.toPlainText().strip()
        if not text and not self._image:
            self.hint.setText("写点内容，或者截一张图")
            self.hint.show()
            return
        due = self._due_epoch()
        image = _copy_image(self._image) if self._image else ""
        rule = str(self.repeat.currentData() or recurrence.NONE)
        item = self.store.add(text or "看图提醒", due, image, recurrence_rule=rule)
        self.hide()
        self.saved.emit(item.id)
