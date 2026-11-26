"""提醒的界面：新建弹窗、列表弹窗、到点内容泡，以及驱动它们的 ReminderCenter。

三个窗口共用同一套深色卡片样式，都贴着岛弹，避免和主界面两套视觉。
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import (
    QDateTime, QObject, QRect, QTime, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QGuiApplication, QKeySequence, QPainter, QBrush, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QDateTimeEdit, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core import config
from .image_viewer import show_image
from .popup_layout import place_popup
from .reminder_drawer import ReminderDrawer
from core.reminders import Reminder, ReminderStore

BG = "#1c1c1e"
CARD = "#242426"
TEXT = "#f2f2f7"
SUBTEXT = "#98989f"
ACCENT = "#0a84ff"

CHIP = ("QPushButton{border:none;border-radius:8px;background:#2c2c2e;"
        "color:#d1d1d6;font-size:11px;padding:5px 12px;}"
        "QPushButton:hover{background:#3a3a3c;color:#ffffff;}")


def _copy_image(src: str) -> str:
    """把截图复制进配置目录，提醒列表删条目时只删副本，不动原始截图。"""
    taken = False
    try:
        d = config.config_dir() / "reminders"
        d.mkdir(parents=True, exist_ok=True)
        name = f"rem_{int(time.time() * 1000)}.png"
        dst = d / name
        shutil.copyfile(src, dst)
        taken = True
        return str(dst)
    except OSError:
        return "" if taken else src


class _Card(QWidget):
    """透明外框 + 圆角卡片，子类往 self.body 里加东西。"""

    def __init__(self, width: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(width)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        card = QWidget(self)
        card.setObjectName("card")
        card.setStyleSheet(f"#card{{background:{BG};border-radius:16px;}}")
        outer.addWidget(card)
        self.body = QVBoxLayout(card)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(9)

    def popup_near(self, anchor: QRect) -> None:
        self.adjustSize()
        place_popup(self, anchor)
        self.show()
        self.raise_()


class ReminderDialog(_Card):
    """新建提醒：内容 + 时间 + 可选截图附件。"""

    saved = Signal(str)

    def __init__(self, store: ReminderStore, snip=None, parent=None) -> None:
        super().__init__(340, parent)
        self.store = store
        self.snip = snip
        self._image = ""
        self._capturing = False

        title = QLabel("新建提醒")
        title.setStyleSheet(f"color:{TEXT}; font-size:14px; font-weight:600;")
        self.body.addWidget(title)

        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("要提醒什么？")
        self.text.setFixedHeight(62)
        self.text.setStyleSheet(
            f"QPlainTextEdit{{background:{CARD};color:{TEXT};border:none;"
            "border-radius:9px;padding:7px;font-size:12px;}")
        self.body.addWidget(self.text)

        when_row = QHBoxLayout()
        when_row.setSpacing(8)
        lab = QLabel("时间")
        lab.setStyleSheet(f"color:{SUBTEXT}; font-size:11px;")
        when_row.addWidget(lab)
        self.when = QDateTimeEdit(self._minute_time(
            QDateTime.currentDateTime().addSecs(600)))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.when.setStyleSheet(
            f"QDateTimeEdit{{background:{CARD};color:{TEXT};border:none;"
            "border-radius:8px;padding:5px 8px;font-size:12px;}"
            "QDateTimeEdit::drop-down{border:none;width:16px;}")
        when_row.addWidget(self.when, 1)
        self.body.addLayout(when_row)

        attach_row = QHBoxLayout()
        attach_row.setSpacing(8)
        self.btn_shot = QPushButton("截图作为提醒")
        self.btn_shot.setStyleSheet(CHIP)
        self.btn_shot.setCursor(Qt.PointingHandCursor)
        self.btn_shot.clicked.connect(self._capture)
        attach_row.addWidget(self.btn_shot)
        self.thumb = QLabel()
        self.thumb.setFixedSize(56, 40)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet(f"QLabel{{background:{CARD};border-radius:7px;}}")
        self.thumb.setCursor(Qt.PointingHandCursor)
        self.thumb.setToolTip("点一下看大图")
        self.thumb.mousePressEvent = lambda _e: show_image(self._image)
        self.thumb.hide()
        attach_row.addWidget(self.thumb)
        self.btn_clear_img = QPushButton("移除图")
        self.btn_clear_img.setStyleSheet(CHIP)
        self.btn_clear_img.setCursor(Qt.PointingHandCursor)
        self.btn_clear_img.clicked.connect(self._clear_image)
        self.btn_clear_img.hide()
        attach_row.addWidget(self.btn_clear_img)
        attach_row.addStretch(1)
        self.body.addLayout(attach_row)

        self.hint = QLabel("")
        self.hint.setStyleSheet("color:#ff9f0a; font-size:10px;")
        self.hint.hide()
        self.body.addWidget(self.hint)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setStyleSheet(CHIP)
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.hide)
        btns.addWidget(self.btn_cancel)
        self.btn_save = QPushButton("保存")
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setStyleSheet(
            "QPushButton{border:none;border-radius:8px;background:%s;"
            "color:#fff;font-size:12px;padding:6px 16px;}"
            "QPushButton:hover{background:#3a9bff;}" % ACCENT)
        self.btn_save.clicked.connect(self._save)
        btns.addWidget(self.btn_save)
        self.body.addLayout(btns)
        if self.snip is not None:
            self.snip.finished.connect(self._on_snip_finished)
        # 写完直接 Ctrl+回车保存，不用再去点按钮
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._save)
        self.adjustSize()

    def _on_snip_finished(self) -> None:
        """截图取消了（Esc/右键）也要把新建窗口放回来。"""
        if self._capturing:
            self._capturing = False
            self.show()
            self.raise_()
            self.activateWindow()

    # ---- 打开 ----
    def open_new(self, image_path: str = "") -> None:
        self.text.clear()
        self.when.setDateTime(self._minute_time(
            QDateTime.currentDateTime().addSecs(600)))
        self.hint.hide()
        self.set_image(image_path)
        self.adjustSize()
        self.text.setFocus()
        self.show()
        self.raise_()
        self.activateWindow()

    def set_image(self, path: str) -> None:
        self._image = path or ""
        if self._image:
            pix = QPixmap(self._image)
            if not pix.isNull():
                self.thumb.setPixmap(pix.scaled(56, 40, Qt.KeepAspectRatio,
                                                Qt.SmoothTransformation))
                self.thumb.show()
                self.btn_clear_img.show()
                self.adjustSize()
                return
        self._image = ""
        self.thumb.hide()
        self.btn_clear_img.hide()
        try:
            self.adjustSize()
        except Exception:
            pass

    def _clear_image(self) -> None:
        self.set_image("")

    def _capture(self) -> None:
        if self.snip is None:
            self.hint.setText("截图不可用")
            self.hint.show()
            return
        self._capturing = True
        self.hide()
        self.snip.start(on_capture=self._on_captured, offer_reminder=False)

    def _on_captured(self, _pix, path: str) -> None:
        self._capturing = False
        if path:
            self.set_image(path)
        self.show()
        self.raise_()
        self.activateWindow()

    # ---- 保存 ----
    @staticmethod
    def _minute_time(dt: QDateTime) -> QDateTime:
        """界面只显示到分钟，秒必须归零，不能把打开弹窗时的秒带进提醒。"""
        t = dt.time()
        return QDateTime(dt.date(), QTime(t.hour(), t.minute(), 0),
                         dt.timeSpec())

    def _due_epoch(self) -> float:
        return self._minute_time(self.when.dateTime()).toPython().timestamp()

    def _save(self) -> None:
        text = self.text.toPlainText().strip()
        if not text and not self._image:
            self.hint.setText("写点内容，或者截一张图")
            self.hint.show()
            return
        due = self._due_epoch()
        image = _copy_image(self._image) if self._image else ""
        item = self.store.add(text or "看图提醒", due, image)
        self.hide()
        self.saved.emit(item.id)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)


class _Row(QWidget):
    done_clicked = Signal(str)
    remove_clicked = Signal(str)

    def __init__(self, item: Reminder, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.setFixedHeight(66 if item.image else 54)
        self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 6, 6)
        lay.setSpacing(8)

        if item.image and Path(item.image).exists():
            thumb = QLabel()
            thumb.setFixedSize(44, 44)
            thumb.setAlignment(Qt.AlignCenter)
            pix = QPixmap(item.image)
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(44, 44, Qt.KeepAspectRatio,
                                           Qt.SmoothTransformation))
            thumb.setStyleSheet("QLabel{background:#2c2c2e;border-radius:7px;}")
            thumb.setCursor(Qt.PointingHandCursor)
            thumb.setToolTip("点一下看大图（滚轮缩放，拖动平移）")
            thumb.mousePressEvent = lambda _e, p=item.image: show_image(p)
            lay.addWidget(thumb)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        head = QLabel(item.text or "看图提醒")
        head.setStyleSheet(
            f"color:{SUBTEXT if item.done else TEXT}; font-size:12px;"
            + ("text-decoration:line-through;" if item.done else ""))
        head.setWordWrap(True)
        meta = QLabel(("已完成 · " if item.done else "") + item.due_text()
                      + (" · 已过期" if item.overdue else ""))
        meta.setStyleSheet(
            f"color:{'#ff9f0a' if item.overdue else SUBTEXT}; font-size:10px;")
        col.addWidget(head, 1)
        col.addWidget(meta)
        lay.addLayout(col, 1)

        if not item.done:
            ok = QPushButton("✓")
            ok.setFixedSize(22, 22)
            ok.setCursor(Qt.PointingHandCursor)
            ok.setToolTip("标记完成")
            ok.setStyleSheet(
                "QPushButton{border:none;background:transparent;color:#30d158;"
                "font-size:13px;}QPushButton:hover{color:#ffffff;}")
            ok.clicked.connect(lambda: self.done_clicked.emit(self.item.id))
            lay.addWidget(ok)
        rm = QPushButton("✕")
        rm.setFixedSize(20, 22)
        rm.setCursor(Qt.PointingHandCursor)
        rm.setToolTip("删除这条提醒")
        rm.setStyleSheet(
            f"QPushButton{{border:none;background:transparent;color:{SUBTEXT};"
            "font-size:12px;}QPushButton:hover{color:#ff453a;}")
        rm.clicked.connect(lambda: self.remove_clicked.emit(self.item.id))
        lay.addWidget(rm)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(255, 255, 255, 8)))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect().adjusted(0, 1, 0, -1), 8, 8)
        p.end()


class ReminderListPanel(_Card):
    """已有提醒的列表：未完成在上，可标完成、删除，也能直接新建。"""

    def __init__(self, store: ReminderStore, on_new: Optional[Callable[[], None]] = None,
                 parent=None) -> None:
        super().__init__(340, parent)
        self.store = store
        self._on_new = on_new

        head = QHBoxLayout()
        self.title = QLabel("提醒")
        self.title.setStyleSheet(f"color:{TEXT}; font-size:13px; font-weight:600;")
        head.addWidget(self.title)
        head.addStretch(1)
        self.btn_new = QPushButton("新建")
        self.btn_new.setStyleSheet(CHIP)
        self.btn_new.setCursor(Qt.PointingHandCursor)
        self.btn_new.clicked.connect(self._new)
        head.addWidget(self.btn_new)
        self.body.addLayout(head)

        self.scroll = QScrollArea()
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
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        self.rows.addStretch(1)
        self.scroll.setWidget(self.body_w)
        self.body.addWidget(self.scroll, 1)
        self._show_done = False
        self._fit_height(0)

    def _fit_height(self, content: int) -> None:
        """高度贴着条目走：两三条就别撑一块空面板。"""
        self.setFixedHeight(max(120, min(340, 58 + content)))

    def _new(self) -> None:
        self.hide()
        if self._on_new is not None:
            self._on_new()

    def refresh(self) -> None:
        while self.rows.count():
            it = self.rows.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        items = self.store.items()
        pending = [i for i in items if not i.done]
        done = [i for i in items if i.done]
        self.title.setText(f"提醒 · {len(items)} 条")
        content = 0
        added = 0
        if not items:
            tip = QLabel("还没有提醒，点右上「新建」")
            tip.setAlignment(Qt.AlignCenter)
            tip.setStyleSheet(f"color:{SUBTEXT}; font-size:12px; padding:30px 0;")
            self.rows.addWidget(tip)
            content = 70
        for item in pending[:40]:
            row = _Row(item, self.body_w)
            row.done_clicked.connect(self._done)
            row.remove_clicked.connect(self._remove)
            self.rows.addWidget(row)
            content += row.height()
            added += 1
        if done:
            more = QPushButton(
                ("收起已完成" if self._show_done else f"已完成 · {len(done)} 条"))
            more.setCursor(Qt.PointingHandCursor)
            more.setStyleSheet(
                f"QPushButton{{border:none;background:transparent;color:{SUBTEXT};"
                "font-size:11px;padding:4px 2px;text-align:left;}"
                "QPushButton:hover{color:#ffffff;}")
            more.clicked.connect(self._toggle_done)
            self.rows.addWidget(more)
            content += 24
            added += 1
            if self._show_done:
                for item in done[:20]:
                    row = _Row(item, self.body_w)
                    row.remove_clicked.connect(self._remove)
                    self.rows.addWidget(row)
                    content += row.height() + 2
                    added += 1
        self.rows.addStretch(1)
        if added > 1:
            content += 2 * (added - 1)
        self._fit_height(content)

    def _toggle_done(self) -> None:
        self._show_done = not self._show_done
        self.refresh()

    def _done(self, rid: str) -> None:
        self.store.mark_done(rid)
        self.refresh()

    def _remove(self, rid: str) -> None:
        self.store.remove(rid)
        self.refresh()

    def popup_near(self, anchor: QRect) -> None:
        self.refresh()
        super().popup_near(anchor)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)


class ReminderBubble(_Card):
    """到点的内容泡：小铃铛 + 内容 +（可选）截图缩略图，必须手动处理掉。"""

    def __init__(self, item: Reminder,
                 on_done: Callable[[str], None],
                 on_snooze: Callable[[str], None],
                 on_close: Optional[Callable[[str], None]] = None,
                 parent=None) -> None:
        super().__init__(330, parent)
        self.item = item
        self._on_done = on_done
        self._on_snooze = on_snooze
        self._on_close = on_close
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        head = QHBoxLayout()
        bell = QLabel("🔔")
        bell.setStyleSheet("font-size:14px;")
        head.addWidget(bell)
        lab = QLabel("提醒")
        lab.setStyleSheet(f"color:{TEXT}; font-size:13px; font-weight:600;")
        head.addWidget(lab)
        head.addStretch(1)
        self.time_lab = QLabel(item.due_text())
        self.time_lab.setStyleSheet(f"color:{SUBTEXT}; font-size:10px;")
        head.addWidget(self.time_lab)
        close = QPushButton("✕")
        close.setFixedSize(20, 20)
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet(
            f"QPushButton{{border:none;background:transparent;color:{SUBTEXT};"
            "font-size:11px;}QPushButton:hover{color:#fff;}")
        close.clicked.connect(self._close)
        head.addWidget(close)
        self.body.addLayout(head)

        self.text_lab = QLabel(item.text or "看图提醒")
        self.text_lab.setWordWrap(True)
        self.text_lab.setStyleSheet(f"color:{TEXT}; font-size:13px;")
        self.body.addWidget(self.text_lab)

        if item.image and Path(item.image).exists():
            pix = QPixmap(item.image)
            if not pix.isNull():
                self.preview = QLabel()
                self.preview.setPixmap(pix.scaled(300, 200, Qt.KeepAspectRatio,
                                                  Qt.SmoothTransformation))
                self.preview.setStyleSheet("QLabel{background:#2c2c2e;border-radius:10px;}")
                self.preview.setCursor(Qt.PointingHandCursor)
                self.preview.setToolTip("点一下看大图（滚轮缩放，拖动平移）")
                self.preview.mousePressEvent = lambda _e: self._open_image()
                self.body.addWidget(self.preview)

        btns = QHBoxLayout()
        snooze = QPushButton("5 分钟后再提醒")
        snooze.setToolTip("点了会在 5 分钟后重新弹一次")
        snooze.setStyleSheet(CHIP)
        snooze.setCursor(Qt.PointingHandCursor)
        snooze.clicked.connect(lambda: self._snooze())
        btns.addWidget(snooze)
        btns.addStretch(1)
        done = QPushButton("完成")
        done.setCursor(Qt.PointingHandCursor)
        done.setStyleSheet(
            "QPushButton{border:none;border-radius:8px;background:%s;color:#fff;"
            "font-size:12px;padding:6px 18px;}"
            "QPushButton:hover{background:#3a9bff;}" % ACCENT)
        done.clicked.connect(lambda: self._done())
        btns.addWidget(done)
        self.body.addLayout(btns)

    def _open_image(self) -> None:
        show_image(self.item.image)

    def _done(self) -> None:
        self._on_done(self.item.id)
        self.hide()
        self.deleteLater()

    def _snooze(self) -> None:
        self._on_snooze(self.item.id)
        self.hide()
        self.deleteLater()

    def _close(self) -> None:
        if self._on_close is not None:
            self._on_close(self.item.id)
        self.hide()
        self.deleteLater()


class ReminderCenter(QObject):
    """提醒的总控：定时检查、弹内容泡、把角标数同步给胶囊上的铃铛。"""

    badge_changed = Signal(int)
    due = Signal()

    def __init__(self, store: ReminderStore, snip=None,
                 anchor_provider: Optional[Callable[[], QRect]] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.snip = snip
        self._anchor = anchor_provider
        self._alt_anchor: Optional[Callable[[], QRect]] = None
        self._bubble: Optional[ReminderBubble] = None
        from .recur_dialog import RecurringReminderDialog
        self.dialog = RecurringReminderDialog(store, snip)
        self.dialog.saved.connect(lambda _rid: self._after_change())
        self.list_panel = ReminderDrawer(
            store, on_changed=self._after_change, on_new=self.new_reminder)

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.check)
        # 提醒精确到分钟，每秒检查一次，避免 10 秒轮询把 :00 拖到 :09。
        self.timer.start(1000)

    # ---- 入口 ----
    def set_alt_anchor(self, provider: Optional[Callable[[], QRect]]) -> None:
        """小伙伴露着时，弹窗和内容泡优先贴它（返回空矩形就退回贴胶囊）。"""
        self._alt_anchor = provider

    def _anchor_rect(self) -> QRect:
        if self._alt_anchor is not None:
            try:
                rect = self._alt_anchor()
                if rect is not None and rect.width() > 0 and rect.height() > 0:
                    return rect
            except Exception:
                pass
        if self._anchor is not None:
            try:
                return self._anchor()
            except Exception:
                pass
        scr = QGuiApplication.primaryScreen()
        geo = scr.availableGeometry() if scr else QRect(0, 0, 1280, 720)
        return QRect(geo.center().x() - 120, geo.top() + 8, 240, 56)

    def new_reminder(self, image_path: str = "") -> None:
        self.list_panel.hide()
        self.dialog.popup_near(self._anchor_rect())
        self.dialog.open_new(image_path)

    def open_list(self) -> None:
        self.dialog.hide()
        self.list_panel.popup_near(self._anchor_rect())

    def new_from_image(self, path: str) -> None:
        self.new_reminder(path)

    def reposition(self, anchor: QRect) -> None:
        """岛展开/收起换了尺寸，已经打开的浮层跟着挪，别被压在面板底下。"""
        for win in (self.dialog, self.list_panel):
            if win.isVisible():
                win.popup_near(anchor)
        if self._bubble is not None and self._bubble.isVisible():
            self._bubble.popup_near(anchor)

    def hide_all(self) -> None:
        """别的浮层要开了，先把提醒这几个收起来，避免同时压两块。"""
        self.dialog.hide()
        self.list_panel.hide()

    # ---- 检查 ----
    def check(self) -> None:
        self.badge_changed.emit(self.store.overdue_count())
        if self._bubble is not None:
            if self._bubble.isVisible():
                return
            # 窗口被系统/其他方式关掉时，别让旧引用把后续提醒永久卡住
            self._bubble = None
        due = self.store.due_now()
        if not due:
            return
        item = due[0]
        self.store.mark_notified(item.id)
        self._show_bubble(item)
        self.due.emit()

    def _show_bubble(self, item: Reminder) -> None:
        self._bubble = ReminderBubble(
            item,
            on_done=self._on_done,
            on_snooze=self._on_snooze,
            on_close=self._on_close,
        )
        self._bubble.popup_near(self._anchor_rect())

    def _on_done(self, rid: str) -> None:
        self.store.mark_done(rid)
        self._bubble = None
        self._after_change()

    def _on_snooze(self, rid: str) -> None:
        self.store.snooze(rid, 300)
        self._bubble = None
        self._after_change()

    def _on_close(self, _rid: str) -> None:
        # 只关掉内容泡就算看过了：条目仍留在列表里，但铃铛角标不再亮
        self.store.mark_acked(_rid)
        self._bubble = None
        self.badge_changed.emit(self.store.overdue_count())

    def _after_change(self) -> None:
        self.badge_changed.emit(self.store.overdue_count())
        QTimer.singleShot(300, self.check)

