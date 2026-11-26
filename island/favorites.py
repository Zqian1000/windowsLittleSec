"""网站收藏浮层：点一行用默认浏览器打开，右键/叉号删除，底部直接添加。

面板只负责展示和交互，数据在 ``core.favorites``。位置由 ``PopupCard``
统一交给公共浮层布局器，不在这里单独算坐标。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from .panels import PopupCard, BG, TEXT, SUBTEXT, ACCENT
from core.favorites import FavoriteStore, Website

ROW_H = 48
LIST_H = 330
STAR = "#ffd60a"


class SiteRow(QWidget):
    opened = Signal(str)
    removed = Signal(str)
    star_changed = Signal(str)

    def __init__(self, item: Website, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.setObjectName("siteRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(ROW_H)
        self.setCursor(Qt.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 5, 6, 5)
        row.setSpacing(9)

        badge = QLabel((item.name[:1] or "网").upper())
        badge.setFixedSize(28, 28)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            "background:%s;color:#fff;border-radius:14px;font-size:12px;"
            "font-weight:600;" % ACCENT)
        row.addWidget(badge)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        name = QLabel(item.name)
        name.setStyleSheet(f"color:{TEXT};font-size:12px;font-weight:600;")
        url = QLabel(item.display_url)
        url.setStyleSheet(f"color:{SUBTEXT};font-size:10px;")
        col.addWidget(name)
        col.addWidget(url)
        row.addLayout(col, 1)

        star = QPushButton("★" if item.starred else "☆")
        star.setFixedSize(22, 22)
        star.setCursor(Qt.PointingHandCursor)
        star.setStyleSheet(
            f"QPushButton{{border:none;background:transparent;"
            f"color:{STAR if item.starred else SUBTEXT};font-size:15px;"
            "border-radius:7px;}"
            f"QPushButton:hover{{color:{STAR};background:#3a3a3c;}}")
        star.clicked.connect(lambda: self.star_changed.emit(self.item.id))
        row.addWidget(star)

        arrow = QLabel("↗")
        arrow.setStyleSheet(f"color:{SUBTEXT};font-size:13px;")
        row.addWidget(arrow)

        remove = QPushButton("×")
        remove.setFixedSize(22, 22)
        remove.setStyleSheet(
            "QPushButton{border:none;background:transparent;color:#8e8e93;"
            "font-size:13px;border-radius:7px;}"
            "QPushButton:hover{color:#ff453a;background:#3a3a3c;}")
        remove.clicked.connect(lambda: self.removed.emit(self.item.id))
        row.addWidget(remove)

        bg = "#2b2721" if item.starred else "#242426"
        hover = "#373126" if item.starred else "#303034"
        self.setStyleSheet(
            f"QWidget#siteRow{{background:{bg};border-radius:9px;}}"
            f"QWidget#siteRow:hover{{background:{hover};}}")

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.opened.emit(self.item.id)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class FavoritesPanel(PopupCard):
    """网站收藏卡片。保持 440 宽，和工具卡片共用同一套定位/互斥。"""

    HEIGHT = 500

    def __init__(self, store: FavoriteStore | None = None, parent=None) -> None:
        super().__init__(self.HEIGHT, parent)
        self.store = store or FavoriteStore()
        self._rows: list[SiteRow] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("网站收藏")
        title.setStyleSheet(f"color:{TEXT};font-size:15px;font-weight:600;")
        head.addWidget(title)
        self.count = QLabel("")
        self.count.setStyleSheet(f"color:{SUBTEXT};font-size:10px;")
        head.addWidget(self.count)
        head.addStretch(1)
        tip = QLabel("点击打开 · ★ 星标 · × 删除")
        tip.setStyleSheet(f"color:{SUBTEXT};font-size:10px;")
        head.addWidget(tip)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(LIST_H)
        self.scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:#1c1c1e;width:7px;margin:0;}"
            "QScrollBar::handle:vertical{background:#48484a;border-radius:3px;"
            "min-height:28px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        self.rows = QVBoxLayout(body)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(5)
        self.rows.addStretch(1)
        self.scroll.setWidget(body)
        root.addWidget(self.scroll)

        add_row = QHBoxLayout()
        add_row.setSpacing(7)
        self.input = QLineEdit()
        self.input.setPlaceholderText("粘贴网址，或输入「名称 https://…」")
        self.input.setFixedHeight(32)
        self.input.setStyleSheet(
            "QLineEdit{background:#2c2c2e;border:none;border-radius:8px;"
            f"color:{TEXT};padding:5px 10px;font-size:12px;}}"
            "QLineEdit:focus{background:#323234;}")
        self.input.returnPressed.connect(self._add)
        add_row.addWidget(self.input, 1)

        add = QPushButton("添加")
        add.setFixedSize(54, 32)
        add.setCursor(Qt.PointingHandCursor)
        add.setStyleSheet(
            f"QPushButton{{border:none;border-radius:8px;background:{ACCENT};"
            "color:#fff;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:#3a9bff;}")
        add.clicked.connect(self._add)
        add_row.addWidget(add)
        root.addLayout(add_row)

        self.hint = QLabel("")
        self.hint.setStyleSheet(f"color:{SUBTEXT};font-size:10px;")
        self.hint.setFixedHeight(15)
        root.addWidget(self.hint)

    # ---- 数据 -> 界面 ----
    def refresh(self) -> None:
        while self.rows.count() > 1:
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._rows.clear()

        items = self.store.items()
        starred = sum(1 for item in items if item.starred)
        self.count.setText(
            f"{len(items)} 个" + (f" · {starred} 星标" if starred else ""))
        if not items:
            empty = QLabel("还没有收藏，粘贴一个网址添加")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"color:{SUBTEXT};font-size:11px;padding:60px 0;")
            self.rows.insertWidget(0, empty)
            return
        for index, site in enumerate(items):
            row = SiteRow(site)
            row.opened.connect(self._open)
            row.removed.connect(self._remove)
            row.star_changed.connect(self._toggle_star)
            self._rows.append(row)
            self.rows.insertWidget(index, row)

    def popup_near(self, anchor) -> None:
        self.refresh()
        super().popup_near(anchor)

    # ---- 操作 ----
    def _find(self, item_id: str) -> Website | None:
        return next((i for i in self.store.items() if i.id == item_id), None)

    def _open(self, item_id: str) -> None:
        item = self._find(item_id)
        if item is None:
            return
        if QDesktopServices.openUrl(QUrl(item.url)):
            self.close_panel()
        else:
            self._say("系统没有可用的浏览器", error=True)

    def _remove(self, item_id: str) -> None:
        if self.store.remove(item_id):
            self.refresh()
            self._say("已删除")

    def _toggle_star(self, item_id: str) -> None:
        starred = self.store.toggle_star(item_id)
        if starred is None:
            return
        self.refresh()
        self._say("已星标，优先显示" if starred else "已取消星标")

    def _add(self) -> None:
        text = self.input.text().strip()
        if not text:
            self._say("先输入一个网址", error=True)
            return
        try:
            item, created = self.store.add(text)
        except ValueError as exc:
            self._say(str(exc), error=True)
            return
        self.input.clear()
        self.refresh()
        self._say("已添加" if created else f"已更新「{item.name}」")

    def _say(self, text: str, error: bool = False) -> None:
        self.hint.setText(text)
        self.hint.setStyleSheet(
            f"color:{'#ff9f0a' if error else SUBTEXT};font-size:10px;")
