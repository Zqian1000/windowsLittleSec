"""区域截图：先冻结每一块屏幕，再让用户框选，复制进剪贴板并存一张 PNG。

做法比"边选边截"稳：overlay 打开之前就把整屏抓成位图，框选期间画面
不会因为动画、视频或者窗口刷新而变，框出来的就是按下那一刻的像素。
多屏时每块屏各开一层，各自用自己那块屏的原图，缩放比例不同也不会串。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor, QCursor, QFont, QGuiApplication, QKeySequence, QPainter, QPen, QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget

ACCENT = "#0a84ff"


def default_save_dir() -> Path:
    """优先存到"图片\\小二截图"；没有这个目录就退回配置目录。

    这里只算路径不建目录：第一次真的截图时再建，启动时不往用户目录里丢空文件夹。
    """
    from core import config
    home = Path.home()
    if (home / "Pictures").exists() or (home / "图片").exists():
        return home / "Pictures" / "小二截图"
    return config.config_dir() / "shots"


class _Overlay(QWidget):
    """一块屏上的取景层：整屏压暗，框选区域露出原图。"""

    picked = Signal(object, object)   # (QPixmap, 屏幕)
    cancelled = Signal()

    def __init__(self, screen, shot: QPixmap) -> None:
        super().__init__(None)
        self._screen = screen
        self._shot = shot
        self._origin: Optional[QPoint] = None
        self._cur: Optional[QPoint] = None
        self._done = False
        self._closed = False         # 已发过 picked / cancelled，避免重复收尾

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(screen.geometry())
        self.setWindowTitle("小二截图")
        self.setFocusPolicy(Qt.StrongFocus)
        # Esc 用快捷键再接一道：万一窗口没拿到键盘焦点，也能退出
        self._esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._esc.activated.connect(self._abort)

    # ---- 绘制 ----
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(self.rect(), self._shot)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))

        sel = self._selection()
        if sel is not None and sel.width() > 0 and sel.height() > 0:
            p.setClipRect(sel)
            p.drawPixmap(self.rect(), self._shot)     # 选中区域恢复原亮度
            p.setClipping(False)

            p.setPen(QPen(QColor(ACCENT), 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawRect(sel.adjusted(0, 0, -1, -1))

            sx = self._shot.width() / max(1, self.width())
            sy = self._shot.height() / max(1, self.height())
            text = f"{int(sel.width() * sx)} × {int(sel.height() * sy)}"
            p.setFont(QFont("Microsoft YaHei", 9))
            fw = p.fontMetrics().horizontalAdvance(text) + 12
            box = QRectF(sel.left(), max(0, sel.top() - 24), fw, 20)
            p.setBrush(QColor(0, 0, 0, 170))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(box, 5, 5)
            p.setPen(QColor("#ffffff"))
            p.drawText(box, Qt.AlignCenter, text)

        if sel is None:
            p.setPen(QColor(255, 255, 255, 190))
            p.setFont(QFont("Microsoft YaHei", 10))
            p.drawText(self.rect(), Qt.AlignHCenter | Qt.AlignTop,
                       "\n拖动框选区域，Esc 取消")
        p.end()

    def _selection(self) -> Optional[QRect]:
        if self._origin is None or self._cur is None:
            return None
        return QRect(self._origin, self._cur).normalized()

    # ---- 交互 ----
    def mousePressEvent(self, event) -> None:
        self.activateWindow()
        self.setFocus()
        if event.button() == Qt.RightButton:
            self._abort()
            return
        if event.button() == Qt.LeftButton:
            self._origin = event.position().toPoint()
            self._cur = self._origin
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._origin is not None and event.buttons() & Qt.LeftButton:
            self._cur = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._origin is None:
            return
        self._cur = event.position().toPoint()
        sel = self._selection()
        if sel is None or sel.width() < 6 or sel.height() < 6:
            self._abort()
            return
        pix = self._crop(sel)
        if pix is None or pix.isNull():
            self._abort()
            return
        self._done = True
        self._closed = True
        self.picked.emit(pix, self._screen)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._abort()
            return
        super().keyPressEvent(event)

    def _crop(self, sel: QRect) -> Optional[QPixmap]:
        if self.width() <= 0 or self.height() <= 0:
            return None
        sx = self._shot.width() / self.width()
        sy = self._shot.height() / self.height()
        src = QRect(int(sel.x() * sx), int(sel.y() * sy),
                    max(1, int(sel.width() * sx)), max(1, int(sel.height() * sy)))
        src = src.intersected(self._shot.rect())
        return self._shot.copy(src)

    def _abort(self) -> None:
        if self._closed:
            return
        self._done = True
        self._closed = True
        self.cancelled.emit()


class _Toast(QWidget):
    """截完在鼠标旁边闪一下结果；带「加提醒」入口时可以点一下直接建提醒。"""

    def __init__(self, text: str, action_text: str = "",
                 on_action: Optional[Callable[[], None]] = None) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        card = QWidget(self)
        card.setStyleSheet(
            "QWidget{background:rgba(28,28,30,235);border-radius:9px;}")
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 7, 8, 7)
        row.setSpacing(8)
        lab = QLabel(text)
        lab.setStyleSheet("QLabel{color:#fff;font-size:12px;background:transparent;}")
        row.addWidget(lab)

        if action_text and on_action is not None:
            btn = QPushButton(action_text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{border:none;background:#0a84ff;color:#fff;"
                "font-size:11px;border-radius:7px;padding:4px 10px;}"
                "QPushButton:hover{background:#3a9bff;}")
            btn.clicked.connect(lambda: (on_action(), self.close()))
            row.addWidget(btn)

        hint = card.sizeHint()
        self.resize(hint.width(), hint.height())
        card.setGeometry(0, 0, hint.width(), hint.height())
        cur = QCursor.pos()
        self.move(cur.x() + 14, cur.y() + 14)
        QTimer.singleShot(3200 if action_text else 1600, self.close)


class SnipSession(QObject):
    """一次截图流程：藏岛 → 抓屏 → 框选 → 复制并存盘 → 提示。"""

    captured = Signal(str)      # 保存路径（存盘失败时为空串）
    reminded = Signal(str)      # 截图后点了「加提醒」，带上图片路径
    finished = Signal()         # 截完或取消，调用方据此恢复自己的窗口

    def __init__(self, save_dir: Optional[Path] = None,
                 before_grab: Optional[Callable[[], None]] = None,
                 after_grab: Optional[Callable[[], None]] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.save_dir = Path(save_dir) if save_dir else default_save_dir()
        self._before = before_grab
        self._after = after_grab
        self._overlays: list[_Overlay] = []
        self._on_capture: Optional[Callable[[QPixmap, str], None]] = None
        self._offer = True
        self._hide_at = time.monotonic()

    def is_active(self) -> bool:
        return bool(self._overlays)

    def start(self, on_capture: Optional[Callable[[QPixmap, str], None]] = None,
              offer_reminder: bool = True) -> None:
        if self._overlays:
            return
        self._on_capture = on_capture
        self._offer = offer_reminder
        if self._before is not None:
            try:
                self._before()
            except Exception:
                pass
        # 让位一瞬间：岛的隐藏动画、歌词条的收起都落定后再抓屏
        self._hide_at = time.monotonic()
        QTimer.singleShot(260, self._grab_all)

    def _grab_all(self) -> None:
        if self._overlays:
            return
        app = QApplication.instance()
        if app is None:
            return
        for screen in app.screens():
            try:
                shot = screen.grabWindow(0)
            except Exception:
                continue
            if shot.isNull():
                continue
            ov = _Overlay(screen, shot)
            ov.picked.connect(self._on_picked)
            ov.cancelled.connect(self._on_cancelled)
            self._overlays.append(ov)
        if not self._overlays:
            self._finish()
            return
        for ov in self._overlays:
            ov.show()
            ov.raise_()
            ov.activateWindow()
        self._overlays[0].setFocus()

    # ---- 结果 ----
    def _on_picked(self, pix: QPixmap, _screen) -> None:
        # 不管后面存盘/弹提示哪一步出错，都必须把取景层收掉，
        # 否则窗口会一直压在最上层、Esc 也关不掉（只能任务管理器杀进程）。
        try:
            self._handle_pick(pix)
        except Exception:
            # 剪贴板已经写进去了，这里只保证能退出
            pass
        finally:
            self._finish()

    def _handle_pick(self, pix: QPixmap) -> None:
        from PySide6.QtGui import QGuiApplication as _Q
        if pix.devicePixelRatio() != 1.0:
            pix.setDevicePixelRatio(1.0)     # 复制进剪贴板按原始像素算
        _Q.clipboard().setPixmap(pix)
        path = ""
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            name = time.strftime("小二截图_%Y%m%d_%H%M%S") + ".png"
            target = self.save_dir / name
            if pix.save(str(target), "PNG"):
                path = str(target)
        except Exception:
            path = ""
        size = f"{pix.width()} × {pix.height()}"
        text = f"已复制 {size}" + ("" if not path else " · 已保存")
        try:
            if self._offer and path:
                _Toast(text, "加提醒",
                       lambda: self.reminded.emit(path)).show()
            else:
                _Toast(text).show()
        except Exception:
            pass
        self.captured.emit(path)
        if self._on_capture is not None:
            try:
                self._on_capture(pix, path)
            except Exception:
                pass

    def _on_cancelled(self) -> None:
        self._finish()

    def _finish(self) -> None:
        for ov in self._overlays:
            try:
                ov.close()
            except Exception:
                pass
        self._overlays = []
        self._on_capture = None
        if self._after is not None:
            try:
                self._after()
            except Exception:
                pass
        self.finished.emit()
