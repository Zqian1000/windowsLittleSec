"""小二主窗口：无边框、置顶、圆角悬浮条，收起/展开两态。"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import (
    Qt, QTimer, QPoint, QRect, QPropertyAnimation, QEasingCurve, QRectF, Signal, QUrl,
    QEvent,
)
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QCursor, QGuiApplication, QPixmap,
    QPainterPath, QDesktopServices,
)
from PySide6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QVBoxLayout, QPushButton, QApplication,
    QStackedWidget, QSizePolicy, QLayout,
)

from .image_viewer import current_viewer
from .popup_guard import PopupGuard
from .widgets import ContactButton, MediaButton, CLIENT_COLORS, GlyphButton, HoverTip
from .companion_ui import AgentAvatarButton
from .lyricbar import LyricBar
from .scanner import ScannerWorker
from core import system_info, im, players as players_mod
from core import lyrics as lyrics_mod
from core.media import MediaController
from core.pacing import PacedTimer
from core.unread import FlashWatcher

BG = "#1c1c1e"
TEXT = "#f2f2f7"
SUBTEXT = "#98989f"
BEJSON_URL = "https://www.bejson.com"


def _dbg(msg: str) -> None:
    """排查用：设了 DAOZAI_DEBUG=1 才写日志。"""
    import os
    if os.environ.get("DAOZAI_DEBUG") != "1":
        return
    try:
        from core.config import config_dir
        base = str(config_dir())
        with open(os.path.join(base, "debug.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


class IslandBar(QWidget):
    def __init__(self, cfg: dict, media: MediaController, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.media = media
        self.expanded = False
        self._drag_pos: QPoint | None = None
        self._press_pos: QPoint | None = None
        self._moved = False
        self._drag_grace_until = 0.0      # 刚拖完的一小段时间里不自动吸回
        self._last_active = time.monotonic()
        self._idle_collapse_sec = int(cfg.get("expanded_idle_sec", 30) or 0)
        self._im_accounts: list[im.IMAccount] = []
        self._im_signature: tuple | None = None
        self.coll_buttons: dict = {}
        self.unread = FlashWatcher()
        self._has_music = False          # 有没有在放歌：决定展开态左边占多高

        # 工具入口（剪贴板 / 截图 / 提醒）由 main 装配，没装配时按钮置灰
        self.clip_panel = None
        self.cheatsheet = None          # Excel 快捷键浮层，首次点开时才建
        self.toolbox = None             # 工具箱面板，首次点开时才建
        self.project_panel = None       # 项目目录
        self.app_panel = None           # 应用快开（开始菜单应用列表）
        self.incident_panel = None      # 报错现场包
        self.clerk_panel = None         # 文员工具
        self.favorites_panel = None     # 网站收藏，首次点开时才建
        self._toast = None
        self._hover_tip = None
        self._toolbox_open = False
        from .toolbox import ALL_TOOLS, DEFAULT_TOOLS
        picked = cfg.get("capsule_tools")
        self._selected_tools = [k for k in (picked if isinstance(picked, list)
                                            else DEFAULT_TOOLS)
                                if k in ALL_TOOLS] or list(DEFAULT_TOOLS)
        # 老配置里没有新加的「网站收藏」：升级后自动出现在胶囊上一次；
        # 之后用户再在工具箱里取消，不会被再次加回来。
        if not cfg.get("fav_tool_introduced"):
            if "fav" not in self._selected_tools:
                self._selected_tools.append("fav")
            cfg["capsule_tools"] = list(self._selected_tools)
            cfg["fav_tool_introduced"] = True
            try:
                from core import config as config_mod
                config_mod.save(cfg)
            except Exception:
                pass
        self.clip_watcher = None
        self.snip = None
        self.reminder_center = None
        self._visible_before_snip = True
        self._runner = None              # 连点 20 次打开的隐藏跑酷窗
        self._runner_was_visible = False

        # 所有工具浮层共用一套"点到别处就收"的规则，见 popup_guard.py
        self._popup_guard = PopupGuard(self._popup_widgets, self)

        # 靠边隐藏状态
        self._anchor: QPoint | None = None
        self._auto_hidden = False
        self._leave_t: float | None = time.monotonic() + 1.2
        self._keep_out_until = 0.0        # 刚滑出来时多留一会儿，别让用户追不上
        self._anim: QPropertyAnimation | None = None

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("小二")
        self.setFixedSize(250, 56)

        self._build_ui()
        self._place_top_center()

        self.lyric_bar = LyricBar()
        self._lyric_key = ""
        self._cover_key = ""
        self._cover_pix: QPixmap | None = None

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.refresh_clock)
        self.clock_timer.start(1000)

        # 界面线程只留轻量刷新，并按实测耗时自适应（见 core/pacing.py）；
        # 窗口枚举和红点采样都搬到后台线程（island/scanner.py），
        # 慢机器上不会因为一次 UIA 查找就把岛卡住。
        self.media_timer = PacedTimer(500, 1600, self._refresh_media, "播放状态", self)
        self.lyric_timer = PacedTimer(600, 1800, self.refresh_lyric, "歌词", self)
        self.edge_timer = PacedTimer(120, 400, self._edge_tick, "靠边隐藏", self)
        self.paced_timers = [self.media_timer, self.lyric_timer, self.edge_timer]
        for _t in self.paced_timers:
            _t.start(_t.interval())

        # 展开态闲置计时：鼠标/键盘在岛上停手一段时间后自己收回收起态
        self.idle_timer = QTimer(self)
        self.idle_timer.setInterval(1000)
        self.idle_timer.timeout.connect(self._idle_tick)
        self.idle_timer.start()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        # 后台扫描：发现窗口/红点变化后发信号回来，界面在 _on_scan 里重建
        self.scanner = ScannerWorker(self.cfg, self.unread, self)
        self.scanner.scanned.connect(self._on_scan)
        self.scanner.start()

        # 有新消息就闪对应图标，直到消息被读掉；读掉前也不自动缩回
        self._unread_prev: dict = {}
        self._blink_keys: set = set()
        self._blink_on = False
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self._blink_tick)
        self.blink_timer.start(650)

        # 有哪一路明显变慢就记一笔（%APPDATA%\Xiaoer\perf.log），换机器排查用
        self.perf_timer = QTimer(self)
        self.perf_timer.timeout.connect(self._log_pacing)
        self.perf_timer.start(30000)

        self.refresh_clock()
        self.refresh_poll()

    def shutdown(self) -> None:
        """退出前停掉后台扫描，避免线程带着 Qt 对象一起被销毁。"""
        try:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
        except Exception:
            pass
        try:
            self.scanner.stop()
        except Exception:
            pass
        self.lyric_bar.hide()
        if self._runner is not None:
            try:
                self._runner.close()
            except Exception:
                pass

    # ---------- UI ----------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSizeConstraint(QLayout.SetNoConstraint)
        self.stack = QStackedWidget()
        # 展开/收起尺寸差很大，必须允许栈和子页被压小，否则切回收起态时
        # 布局的最小尺寸会把窗口撑在展开尺寸上。
        self.stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.stack.setMinimumSize(0, 0)
        root.addWidget(self.stack)

        self.collapsed = self._build_collapsed()
        self.expanded_page = self._build_expanded()
        for page in (self.collapsed, self.expanded_page):
            page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
            page.setMinimumSize(0, 0)
        self.stack.addWidget(self.collapsed)
        self.stack.addWidget(self.expanded_page)

    def _build_collapsed(self) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(14, 10, 16, 10)
        row.setSpacing(12)

        # 胶囊上的工具：具体显示哪几个由工具箱里勾选决定，齿轮永远在
        tool_row = QHBoxLayout()
        tool_row.setSpacing(3)
        self.btn_clip = GlyphButton("clip", "剪贴板历史", "剪贴板历史")
        self.btn_snip = GlyphButton("snip", "区域截图", "区域截图")
        self.btn_bell = GlyphButton("bell", "新建提醒", "新建提醒")
        self.btn_rlist = GlyphButton("list", "提醒列表", "提醒列表")
        self.btn_fav = GlyphButton("globe", "网站收藏", "网站收藏")
        self.btn_json = GlyphButton("json", f"在浏览器打开 bejson（{BEJSON_URL}）", "Bejson")
        self.btn_xls = GlyphButton("excel", "Excel 快捷键大全（可钉住）", "Excel 快捷键")
        self.btn_proj = GlyphButton("apps", "应用快开：开始菜单应用直接列出，点一下启动", "应用快开")
        self.btn_incident = GlyphButton("alert", "报错现场包：窗口/进程/剪贴板拼成 Markdown", "报错现场包")
        self.btn_clerk = GlyphButton("doc", "文员工具：金额、日期、重命名、清单、文本", "文员工具")
        self._tool_buttons = {
            "clip": self.btn_clip,
            "snip": self.btn_snip,
            "bell": self.btn_bell,
            "list": self.btn_rlist,
            "fav": self.btn_fav,
            "bejson": self.btn_json,
            "excel": self.btn_xls,
            "proj": self.btn_proj,
            "incident": self.btn_incident,
            "clerk": self.btn_clerk,
        }
        self.btn_clip.clicked.connect(self.open_clipboard)
        self.btn_snip.clicked.connect(self.start_snip)
        self.btn_bell.clicked.connect(self.open_reminder_new)
        self.btn_rlist.clicked.connect(self.open_reminder_list)
        self.btn_fav.clicked.connect(self.open_favorites)
        self.btn_json.clicked.connect(self.open_bejson)
        self.btn_xls.clicked.connect(self.open_cheatsheet)
        self.btn_proj.clicked.connect(self.open_projects)
        self.btn_incident.clicked.connect(self.open_incident)
        self.btn_clerk.clicked.connect(self.open_clerk)
        for key in ("clip", "snip", "bell", "list", "fav", "bejson", "excel",
                    "proj", "incident", "clerk"):
            tool_row.addWidget(self._tool_buttons[key])
        for btn in self._tool_buttons.values():
            btn.hovered.connect(self._show_tool_hover)
            btn.pressed.connect(self._hide_tool_hover)
        row.addLayout(tool_row)

        self.coll_time = QLabel("--:--")
        self.coll_time.setStyleSheet(f"color:{TEXT}; font-size:19px; font-weight:600;")
        row.addWidget(self.coll_time)

        # 桌面小伙伴功能暂停：这里先放一个木鱼占位，
        # 不接照片、不生成小人；后续接入用户自有模型时再替换。
        self.btn_agent = AgentAvatarButton()
        self.btn_agent.setToolTip("点一下敲木鱼：功德 +1")
        self.btn_agent.easter_egg.connect(self.open_runner)
        row.addWidget(self.btn_agent)

        row.addStretch(1)

        self.coll_icons = QHBoxLayout()
        self.coll_icons.setSpacing(8)
        row.addLayout(self.coll_icons)
        return w

    def _build_expanded(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(16, 12, 16, 12)
        col.setSpacing(10)

        # 顶部：日期 + 时间
        header = QHBoxLayout()
        self.exp_date = QLabel("")
        self.exp_date.setStyleSheet(f"color:{TEXT}; font-size:14px; font-weight:600;")
        self.exp_time = QLabel("")
        self.exp_time.setStyleSheet(f"color:{SUBTEXT}; font-size:14px;")
        header.addWidget(self.exp_date)
        header.addStretch(1)
        header.addWidget(self.exp_time)
        col.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(12)

        # 音乐面板：歌名在上、封面居中、控制键在下（竖排，避免左侧留大片空白）
        music_box = QVBoxLayout()
        music_box.setSpacing(8)

        self.music_row = QWidget()
        mv = QVBoxLayout(self.music_row)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(3)

        self.music_label = QLabel("暂无音乐")
        self.music_label.setStyleSheet(f"color:{TEXT}; font-size:13px; font-weight:600;")
        self.music_label.setWordWrap(True)      # 长歌名换两行，标题不再被截断
        self.music_label.setMaximumHeight(36)
        self.music_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.artist_label = QLabel("")
        self.artist_label.setStyleSheet(f"color:{SUBTEXT}; font-size:11px;")
        self.artist_label.setWordWrap(False)
        self.artist_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(138, 138)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setStyleSheet(
            "QLabel{background:#2c2c2e;border-radius:12px;}")

        mv.addWidget(self.music_label)
        mv.addWidget(self.artist_label)
        mv.addSpacing(2)
        mv.addWidget(self.cover_label, 0, Qt.AlignHCenter)
        music_box.addWidget(self.music_row, 1)

        # 没有播放会话时：列出本机已装的音乐软件，点一下启动/切到前台
        self.player_box = QWidget()
        self.player_list = QVBoxLayout(self.player_box)
        self.player_list.setContentsMargins(0, 0, 0, 0)
        self.player_list.setSpacing(4)
        music_box.addWidget(self.player_box)

        # 三键不单独占一行，直接压在封面底部那一条上：省掉一行高度
        self.transport = QWidget(self.cover_label)
        self.transport.setObjectName("coverTransport")
        self.transport.setAttribute(Qt.WA_StyledBackground, True)
        self.transport.setStyleSheet(
            "#coverTransport{background:rgba(0,0,0,0.58);"
            "border-bottom-left-radius:12px;border-bottom-right-radius:12px;}")
        self.transport.setGeometry(0, self.cover_label.height() - 38,
                                   self.cover_label.width(), 38)
        controls = QHBoxLayout(self.transport)
        controls.setContentsMargins(8, 2, 8, 2)
        controls.setSpacing(4)
        self.btn_prev = MediaButton("prev")
        self.btn_play = MediaButton("play")
        self.btn_next = MediaButton("next")
        self.btn_prev.clicked.connect(self._on_prev)
        self.btn_play.clicked.connect(self._on_play_pause)
        self.btn_next.clicked.connect(self._on_next)
        controls.addStretch(1)
        controls.addWidget(self.btn_prev)
        controls.addWidget(self.btn_play)
        controls.addWidget(self.btn_next)
        controls.addStretch(1)
        self.transport.raise_()
        body.addLayout(music_box, stretch=6)

        # IM 侧边列
        self.im_column = QVBoxLayout()
        self.im_column.setSpacing(6)
        body.addLayout(self.im_column, stretch=6)

        col.addLayout(body, stretch=1)

        # 底部工具条：第一个是工具箱，其余跟胶囊上勾选的一致（同一套顺序与开关）
        tools = QHBoxLayout()
        tools.setSpacing(6)
        chip = ("QPushButton{border:none;border-radius:8px;background:#2c2c2e;"
                "color:#d1d1d6;font-size:11px;padding:0 10px;}"
                "QPushButton:hover{background:#3a3a3c;color:#ffffff;}"
                "QPushButton:disabled{color:#636366;background:#232325;}")
        self._exp_chip_style = chip
        self._exp_tools_layout = tools

        self.btn_exp_toolbox = QPushButton("工具箱")
        self.btn_exp_toolbox.setFixedHeight(24)
        self.btn_exp_toolbox.setCursor(Qt.PointingHandCursor)
        self.btn_exp_toolbox.setStyleSheet(
            "QPushButton{border:none;border-radius:8px;background:#0a84ff;"
            "color:#ffffff;font-size:11px;font-weight:600;padding:0 12px;}"
            "QPushButton:hover{background:#3a9bff;}")
        self.btn_exp_toolbox.setToolTip("勾选胶囊上显示哪些工具按钮")
        self.btn_exp_toolbox.clicked.connect(self.open_toolbox)
        tools.addWidget(self.btn_exp_toolbox)

        # key → (按钮文案, 提示, 回调)
        spec = {
            "clip": ("剪贴板", "最近复制过的文字，点一条写回剪贴板", self.open_clipboard),
            "snip": ("截图", "框选一块屏幕区域，复制并保存 PNG", self.start_snip),
            "bell": ("提醒", "新建一条提醒，可以配截图", self.open_reminder_new),
            "list": ("提醒列表", "查看已有提醒，标完成或删除", self.open_reminder_list),
            "fav": ("网站", "常用网站收藏，点一下用默认浏览器打开", self.open_favorites),
            "bejson": ("Bejson", f"在浏览器打开 {BEJSON_URL}", self.open_bejson),
            "excel": ("Excel 键", "Excel 快捷键大全（浮层里可以钉住）", self.open_cheatsheet),
            "proj": ("应用", "开始菜单应用列表，点一下启动；可切项目目录", self.open_projects),
            "incident": ("现场", "把窗口/进程/剪贴板拼成 Markdown", self.open_incident),
            "clerk": ("文员", "金额、日期、重命名、清单、文本", self.open_clerk),
        }
        self._exp_chips: dict = {}
        for key in ("clip", "snip", "bell", "list", "fav", "bejson", "excel",
                    "proj", "incident", "clerk"):
            text, tip, handler = spec[key]
            b = QPushButton(text)
            b.setFixedHeight(24)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(chip)
            b.setToolTip(tip)
            b.clicked.connect(handler)
            tools.addWidget(b)
            self._exp_chips[key] = b
        tools.addStretch(1)
        self.snip_hint = QLabel("")
        self.snip_hint.setStyleSheet(f"color:{SUBTEXT}; font-size:10px;")
        tools.addWidget(self.snip_hint)
        col.addLayout(tools)
        self._apply_tool_visibility()
        return w

    # ---------- 布局/定位 ----------
    def _place_top_center(self) -> None:
        screen = self.screen().availableGeometry()
        x = screen.center().x() - self.width() // 2
        self.move(x, screen.top() + 8)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(BG)))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect(), 18, 18)
        p.end()

    # ---------- 交互：拖动 / 切换 ----------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            # 吸附动画还在跑时抓住它：先停在当前可见位置，再按鼠标算偏移，
            # 否则动画会一边往回滑、拖动一边改位置，两边打架。
            self._stop_slide()
            if self._auto_hidden:
                self._auto_hidden = False
                self.move(self._visible_target())
            self._press_pos = event.globalPosition().toPoint()
            self._drag_pos = self._press_pos - self.frameGeometry().topLeft()
            self._moved = False
            self._leave_t = None
            self._last_active = time.monotonic()
            self.setCursor(Qt.ClosedHandCursor)
            self.grabMouse()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            gp = event.globalPosition().toPoint()
            new_pos = gp - self._drag_pos
            # 和按下点比较，而不是和窗口左上角比较：否则轻轻一点也会被判成拖动
            if (gp - self._press_pos).manhattanLength() > 6:
                self._moved = True
            geo = self._screen_geo()
            # 至少留一半在屏幕里，拖到边角也不会彻底找不回来
            min_x, max_x = geo.left() - self.width() // 2, geo.right() - self.width() // 2
            min_y, max_y = geo.top() - 8, geo.bottom() - self.height() // 2
            new_pos.setX(max(min_x, min(max_x, new_pos.x())))
            new_pos.setY(max(min_y, min(max_y, new_pos.y())))
            self.move(new_pos)
            self._last_active = time.monotonic()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            _dbg(f"release moved={self._moved} pos={event.globalPosition().toPoint()}")
            try:
                self.releaseMouse()
            except Exception:
                pass
            self.unsetCursor()
            if not self._moved:
                self._drag_pos = None
                self._leave_t = None
                self.toggle()
                event.accept()
                return
            # 拖动后把当前位置记为新的停靠点；并给 1 秒缓冲，
            # 别刚松手鼠标往外一挪就立刻被吸回屏幕顶。
            self._anchor = self.pos()
            self._drag_pos = None
            self._leave_t = None
            self._drag_grace_until = time.monotonic() + 1.0
            self._last_active = time.monotonic()
            event.accept()

    def _stop_slide(self) -> None:
        if self._anim is not None:
            try:
                self._anim.stop()
            except Exception:
                pass
            self._anim = None

    # ---------- 靠边隐藏 ----------
    def _screen_geo(self):
        scr = self.screen() or QGuiApplication.primaryScreen()
        return scr.availableGeometry()

    def _visible_target(self) -> QPoint:
        if self._anchor is not None:
            return QPoint(self._anchor)
        return QPoint(self.x(), self._screen_geo().top() + 8)

    def visible_rect(self) -> QRect:
        """胶囊“该出现”的矩形，不受靠边隐藏动画影响。

        提醒气泡到点时胶囊可能正藏在上沿；如果拿实际 geometry 当锚点，
        气泡会算到屏幕外或展开后正好压住胶囊。公共布局器和提醒中心都用
        这个矩形作为锚点。
        """
        return QRect(self._visible_target(), self.size())

    def _near_top_edge(self) -> bool:
        """只有停在上沿附近才允许"靠边隐藏"。

        用户把胶囊拖到屏幕中间/下方，是想让它留在那儿；如果还按原来的
        逻辑在鼠标离开后收回屏幕顶，就会表现为"怎么拖都拖不上去/一追就缩"。
        这里用 32px 的感应带，拖出这条带就不再自动收。
        """
        anchor_y = self._anchor.y() if self._anchor is not None else self.y()
        return anchor_y <= self._screen_geo().top() + 32

    def _slide_to(self, target: QPoint) -> None:
        if self.pos() == target:
            return
        if self._anim is not None:
            self._anim.stop()
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(170)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(self.pos())
        anim.setEndValue(target)
        anim.finished.connect(lambda: setattr(self, "_anim", None))
        anim.start()
        self._anim = anim

    def present(self) -> None:
        """再次启动 exe 时把已有的条唤到眼前（躲着的话先滑出来）。"""
        self.show()
        if self._auto_hidden:
            self._show_from_edge()
        self._leave_t = time.monotonic() + 3.0     # 留三秒给人看见，别刚出来又缩回去
        self.raise_()

    def _show_from_edge(self) -> None:
        if not self._auto_hidden:
            return
        self._auto_hidden = False
        self._keep_out_until = time.monotonic() + 1.5
        self.show()
        self._slide_to(self._visible_target())

    def _hide_to_edge(self) -> None:
        if self._auto_hidden or not self._near_top_edge():
            return
        self._hide_tool_hover()
        self._auto_hidden = True
        geo = self._screen_geo()
        self._slide_to(QPoint(self.x(), geo.top() - self.height()))

    def _edge_tick(self) -> None:
        # 彩蛋跑酷开着：胶囊已经藏起来，别让贴顶唤出把它重新盖到游戏上面
        if self._runner is not None:
            self._leave_t = None
            return
        # 工具箱开着：一直露着，等用户点「完成」再恢复靠边隐藏
        if self._toolbox_open:
            self._leave_t = None
            self._show_from_edge()
            return
        # 拖动中：保持可见，绝不能在拖动过程中播放"缩回"动画
        if self._drag_pos is not None:
            self._leave_t = None
            if self._auto_hidden:
                self._auto_hidden = False
                self.move(self._visible_target())
            return
        # 刚拖完 / 刚滑出来：先别吸回，给用户把鼠标移过去的余地
        if (time.monotonic() < self._drag_grace_until
                or time.monotonic() < self._keep_out_until):
            self._leave_t = None
            self._show_from_edge()
            return
        # 展开态：保持可见，收起由 _idle_tick 负责
        if self.expanded:
            self._leave_t = None
            self._show_from_edge()
            return
        # 有工具浮层开着：岛也留在原地，别让卡片孤零零挂在屏幕外
        if self._popup_widgets():
            self._leave_t = None
            self._show_from_edge()
            return
        if not self.cfg.get("edge_hide", True):
            self._show_from_edge()
            return
        # 还有没读的消息：一直露在外面闪，别自己缩回屏幕顶
        if self._blink_keys:
            self._leave_t = None
            self._show_from_edge()
            return

        # 已经拖离顶边感应带：就当普通悬浮窗留在原地，不再自动吸回屏幕顶
        if not self._near_top_edge():
            self._leave_t = None
            return

        cursor = QCursor.pos()
        if self.rect().contains(self.mapFromGlobal(cursor)):
            self._leave_t = None
            self._show_from_edge()
            return

        # 顶边感应带：鼠标贴到时间条上方的一小条就唤出
        geo = self._screen_geo()
        center_x = self.x() + self.width() // 2
        near_top = cursor.y() <= geo.top() + 26
        near_x = abs(cursor.x() - center_x) <= self.width() // 2 + 80
        if near_top and near_x:
            self._leave_t = None
            self._show_from_edge()
            return

        if self._auto_hidden:
            return
        delay = self.cfg.get("edge_hide_delay_ms", 500) / 1000
        if self._leave_t is None:
            self._leave_t = time.monotonic()
        elif time.monotonic() - self._leave_t >= delay:
            self._hide_to_edge()

    def set_show_lyrics(self, enable: bool) -> None:
        """托盘里切换"显示歌词"：关掉就立刻收起，开着按进度重新贴出来。"""
        self.cfg["show_lyrics"] = enable
        if enable:
            self.refresh_lyric()
        else:
            self.lyric_bar.hide()

    def set_edge_hide(self, enable: bool) -> None:
        self.cfg["edge_hide"] = enable
        self._leave_t = None
        if not enable:
            self._show_from_edge()

    def set_idle_collapse(self, seconds: int) -> None:
        """展开态闲置多少秒自动收回；0 = 关闭。"""
        try:
            sec = max(0, int(seconds))
        except (TypeError, ValueError):
            sec = 0
        self._idle_collapse_sec = sec
        self.cfg["expanded_idle_sec"] = sec
        self._last_active = time.monotonic()

    def _idle_tick(self) -> None:
        if not self.expanded or self._idle_collapse_sec <= 0:
            return
        if self._drag_pos is not None or self._toolbox_open:
            self._last_active = time.monotonic()
            return
        if self._popup_widgets():
            # 卡片还开着，说明用户在看内容，不算"没操作"
            self._last_active = time.monotonic()
            return
        if time.monotonic() - self._last_active >= self._idle_collapse_sec:
            _dbg(f"idle {self._idle_collapse_sec}s -> collapse")
            self.collapse()

    def eventFilter(self, obj, event) -> bool:
        """展开态只要鼠标在岛上动/点/滚，就把闲置计时重置。"""
        if self.expanded and event.type() in (
            QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
            QEvent.MouseButtonDblClick, QEvent.Wheel, QEvent.KeyPress,
        ):
            try:
                if self.frameGeometry().contains(QCursor.pos()):
                    self._last_active = time.monotonic()
            except Exception:
                pass
        return super().eventFilter(obj, event)

    def _music_label_h(self) -> int:
        """歌名按当前宽度换行后的实际高度（1 行 18、2 行 36），用来算面板高度。"""
        text = self.music_label.text() or ""
        width = max(120, self.music_row.width() or 200)
        fm = self.music_label.fontMetrics()
        rect = fm.boundingRect(QRect(0, 0, width, 10000), Qt.TextWordWrap, text)
        return max(16, min(36, rect.height()))

    def _expanded_size(self) -> tuple[int, int]:
        """展开态高度贴着内容走：有没有在放歌、歌名几行、账号几条，都不硬撑。"""
        n = max(1, len(self._im_accounts))
        im_h = n * 32 + max(0, n - 1) * 6
        players = self.player_list.count()
        player_h = players * 26 + max(0, players - 1) * 4
        if self._has_music:
            label_h = self._music_label_h()
            artist_h = 15 if self.artist_label.text() else 0
            # 歌名+3+歌手+3+2+3+封面138
            left_h = label_h + artist_h + 3 + 3 + 2 + 3 + 138
        else:
            # 没在放歌：左边只有播放器列表（或一行"无音乐播放器"），不按封面高度硬撑
            left_h = player_h if players else 18
        h = max(88 + left_h, 88 + max(im_h, 18))   # 88 = 原 54 + 工具条 34
        return 440, max(120, min(380, h))

    def _sync_expanded_size(self) -> None:
        """展开态按最新内容收放高度（换歌名行数变了就跟着缩），只动一次。"""
        if not self.expanded:
            return
        w, h = self._expanded_size()
        if (w, h) != (self.width(), self.height()):
            self._force_size(w, h)
            QTimer.singleShot(0, self._reflow_popups)

    def toggle(self) -> None:
        self.expanded = not self.expanded
        _dbg(f"toggle -> expanded={self.expanded}")
        self._last_active = time.monotonic()
        if self.expanded:
            self.stack.setCurrentWidget(self.expanded_page)
            w, h = self._expanded_size()
        else:
            self.stack.setCurrentWidget(self.collapsed)
            w, h = self._collapsed_width(), 56
        self._force_size(w, h)
        self.refresh_poll()
        QTimer.singleShot(0, self._reflow_popups)

    def _reflow_popups(self) -> None:
        """岛尺寸变了：把已经打开的工具浮层重新贴到岛下方，避免和展开面板重叠。"""
        geo = self.visible_rect()
        if self.reminder_center is not None:
            self.reminder_center.reposition(geo)
        if self.clip_panel is not None and self.clip_panel.isVisible():
            self.clip_panel.popup_near(geo)
        if self.cheatsheet is not None and self.cheatsheet.isVisible():
            self.cheatsheet.popup_near(geo)
        if self.toolbox is not None and self.toolbox.isVisible():
            self.toolbox.popup_near(geo)
        if self.favorites_panel is not None and self.favorites_panel.isVisible():
            self.favorites_panel.popup_near(geo)

    def _reminder_windows(self) -> list:
        """提醒中心当前挂着的卡片窗口（弹窗、列表、抽屉……），不写死名字。

        右侧 To Do 抽屉这类后来加的卡片，只要挂在 ReminderCenter 上就会被
        自动纳入互斥和"浮层开着不缩回"的判断，不用每加一张卡就改 bar。
        """
        center = self.reminder_center
        if center is None:
            return []
        out = []
        seen = set()
        for value in vars(center).values():
            if not isinstance(value, QWidget) or value.__class__.__name__ == "ReminderBubble":
                continue
            if id(value) not in seen:
                seen.add(id(value))
                out.append(value)
        return out

    def _popup_widgets(self) -> list:
        """当前露着的工具浮层。到点的内容泡不算：它必须由用户手动处理。"""
        out = []
        for w in (self.cheatsheet, self.clip_panel, self.toolbox,
                  self.favorites_panel, self.app_panel, self.project_panel,
                  self.incident_panel, self.clerk_panel):
            if w is not None and w.isVisible():
                out.append(w)
        for w in self._reminder_windows():
            if w.isVisible():
                out.append(w)
        viewer = current_viewer()
        if viewer is not None:
            out.append(viewer)
        return out

    def _dismiss_others(self, keep=None) -> None:
        """卡片互斥：开一张新卡片前，把其他卡片全收掉。

        每个入口原先各写一套 hide，漏一个就会两张卡叠在一起；统一在这里收口，
        以后新增卡片只要挂在 ReminderCenter 或登记进 `_popup_widgets` 就会自动遵守。
        钉住的速查卡在被"打开另一张卡"时也收：钉住只挡自动收起，不挡换卡。
        """
        wins = [self.cheatsheet, self.clip_panel, self.favorites_panel,
                self.app_panel, self.project_panel, self.incident_panel,
                self.clerk_panel]
        wins += self._reminder_windows()
        for win in wins:
            if win is None or win is keep or not win.isVisible():
                continue
            win.hide()
        if (self.toolbox is not None and self.toolbox is not keep
                and self.toolbox.isVisible()):
            self.toolbox.close_panel()

    def open_runner(self) -> None:
        """木鱼头像连点 20 次的彩蛋：在原位开一条木鱼跑酷。

        宽度取当前胶囊宽度，高度约为胶囊的八成；打开时把胶囊藏起来，视觉上
        就是胶囊变成了一条小游戏。再点一次头像 / Esc 退出后恢复。
        """
        if self._runner is not None:
            try:
                self._runner.close()
            except Exception:
                pass
            return
        from .runner import RunnerGame
        self._dismiss_others()
        w = self.width()
        # 只比胶囊略矮一点：高度太低会把画面压扁，也没法正常跳跃。
        # 目标约胶囊高度的 82%，收起态通常得到 482×57。
        h = max(52, min(72, int(round(self.height() * 0.82))))
        game = RunnerGame(w, h)
        geo = self.geometry()
        game.move(geo.center().x() - w // 2,
                  geo.center().y() - h // 2)
        game.closed.connect(self._on_runner_closed)
        self._runner = game
        self._runner_was_visible = self.isVisible()
        self.hide()
        game.show_game()

    def _on_runner_closed(self) -> None:
        self._runner = None
        if self._runner_was_visible:
            self.show()
            self.raise_()
            self._leave_t = time.monotonic() + 1.2
        self._runner_was_visible = False

    def collapse(self) -> None:
        """展开态收回收起态（点完账号立刻收起，别让浮层压住刚唤起的窗口）。"""
        if self.expanded:
            self.toggle()

    def _force_size(self, w: int, h: int) -> None:
        """先切页再压尺寸：只 setFixedSize 会被布局的最小尺寸顶回来。"""
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.stack.setMinimumSize(0, 0)
        self.stack.setMaximumSize(16777215, 16777215)
        self.stack.setFixedSize(w, h)
        self.setFixedSize(w, h)
        self.layout().activate()

    # ---------- 数据刷新 ----------
    def refresh_clock(self) -> None:
        hhmm, date, week = system_info.clock()
        self.coll_time.setText(hhmm)
        self.exp_time.setText(hhmm)
        self.exp_date.setText(f"{date} {week}")
        # 文字宽度会随字号/内容变，收起态顺手对一次宽度（没变就直接返回）
        if not self.expanded:
            self._apply_collapsed_size()

    def refresh_poll(self) -> None:
        """让后台扫描立刻补一轮（展开/收起、点完图标后用），音乐状态就地刷新。"""
        try:
            self.scanner.kick()
        except Exception:
            pass
        self._refresh_media()

    # ---------- 音乐控制 ----------
    def _on_play_pause(self) -> None:
        """先立刻翻转图标，并把"用户要的状态"告诉控制器：静音时探测慢，别让图标翻回去。"""
        target_playing = self.btn_play.kind != "pause"   # 现在是 ▶ 就是要点播放
        self.btn_play.set_kind("pause" if target_playing else "play")
        self.media.mark_intent(target_playing)
        self.media.toggle_play_pause()
        QTimer.singleShot(450, self._refresh_media)

    def _on_next(self) -> None:
        # 切歌的一瞬间会话会短暂归零，先按住当前状态，别闪一下"未在播放"
        self.media.mark_intent(self.btn_play.kind == "pause")
        self.media.next_track()
        QTimer.singleShot(500, self._refresh_media)

    def _on_prev(self) -> None:
        self.media.mark_intent(self.btn_play.kind == "pause")
        self.media.prev_track()
        QTimer.singleShot(500, self._refresh_media)

    def _refresh_media(self) -> None:
        st = self.media.get_state()
        if st.has_session:
            self._has_music = True
            self.music_row.show()
            self._show_music_parts(True)
            self.music_label.setText(st.title or "正在播放")
            self.music_label.setToolTip(st.title or "")   # 两行也放不下时悬停看全名
            sub = st.artist or st.album or ""
            self.artist_label.setText(sub)
            self.artist_label.setVisible(bool(sub))
            self.music_label.setFixedHeight(self._music_label_h())
            self._sync_expanded_size()
            self._apply_cover(st)
            self.player_box.hide()
            self.btn_play.set_kind("pause" if st.status == "playing" else "play")
            if st.status == "playing":
                lyrics_mod.load_async(st.title, st.artist)
            return

        self._apply_cover(None)
        # 没歌时才扫本机播放器；扫描有进程枚举成本，2 秒一次就够
        now = time.monotonic()
        if (getattr(self, "_players_sig", None) is None
                or now - getattr(self, "_players_at", 0.0) >= 2.0):
            self._players_at = now
            found = players_mod.detect()
            sig = tuple((p.key, p.name, p.running) for p in found)
            if sig != getattr(self, "_players_sig", None):
                self._players_sig = sig
                self._clear_layout(self.player_list)
                for p in found:
                    btn = QPushButton(p.name)
                    btn.setFixedHeight(26)
                    btn.setCursor(Qt.PointingHandCursor)
                    btn.setToolTip("已运行，点击切到前台" if p.running else "点击启动")
                    btn.setStyleSheet(
                        "QPushButton{color:#f2f2f7;background:#2c2c2e;border-radius:6px;"
                        "text-align:left;padding-left:8px;font-size:11px;}"
                        "QPushButton:hover{background:#3a3a3c;}"
                    )
                    btn.clicked.connect(lambda _=False, k=p.key: players_mod.open_player(k))
                    self.player_list.addWidget(btn)
                # 列表贴住顶部，多出来的高度放下面，别把一行按钮挤到正中
                self.player_list.addStretch(1)
        if getattr(self, "_players_sig", ()):
            self.music_row.hide()
            self.player_box.show()
        else:
            self.music_row.show()
            self._show_music_parts(False)      # 只留标题，别留空封面和空按钮
            self.music_label.setText("无音乐播放器")
            self.music_label.show()
            self.player_box.hide()
        self._has_music = False
        self._sync_expanded_size()

    def _show_music_parts(self, full: bool) -> None:
        """有歌时显示歌手/封面/控制键；没有音乐时只留一行标题。"""
        self.artist_label.setVisible(full)
        self.cover_label.setVisible(full)
        self.transport.setVisible(full)

    def _apply_cover(self, st) -> None:
        """把 SMTC 给的封面字节画成圆角缩略图；取不到就画一个音符占位。"""
        key = st.cover_key if st is not None else ""
        if key == self._cover_key and self._cover_pix is not None:
            return
        self._cover_key = key
        self._cover_pix = None
        data = st.cover if st is not None else b""
        size = max(40, min(self.cover_label.width(), self.cover_label.height()))
        pix = QPixmap()
        if data and pix.loadFromData(data):
            scaled = pix.scaled(size, size, Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
            rounded = QPixmap(size, size)
            rounded.fill(Qt.transparent)
            p = QPainter(rounded)
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, size, size), 12, 12)
            p.setClipPath(path)
            x = (scaled.width() - size) // 2
            y = (scaled.height() - size) // 2
            p.drawPixmap(-x, -y, scaled)
            p.end()
            self._cover_pix = rounded
            self.cover_label.setStyleSheet(
                "QLabel{background:transparent;border-radius:12px;}")
            self.cover_label.setPixmap(rounded)
            return
        self.cover_label.setPixmap(QPixmap())
        self.cover_label.setText("♪")
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setStyleSheet(
            "QLabel{background:#2c2c2e;border-radius:12px;color:#8e8e93;font-size:34px;}")

    def _log_pacing(self) -> None:
        """有哪一路刷新明显变慢就记一笔，换机器后查 perf.log 就知道卡在哪。"""
        scanner_slow = (self.scanner.accounts_ms > 2000 or self.scanner.unread_ms > 300)
        if not scanner_slow and not any(
                t.max_cost_ms > 120 or t.interval() > t.base_interval
                for t in self.paced_timers):
            return
        try:
            from core.config import config_dir
            path = config_dir() / "perf.log"
            scan = (f"后台扫描: 枚举 {self.scanner.accounts_ms}ms "
                    f"采样 {self.scanner.unread_ms}ms 单轮 {self.scanner.last_round_ms:.0f}ms")
            with open(path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%m-%d %H:%M:%S") + "  " + scan + " | "
                        + " | ".join(t.stats() for t in self.paced_timers) + "\n")
        except Exception:
            pass
        finally:
            for t in self.paced_timers:
                t.max_cost_ms = 0.0

    def refresh_lyric(self) -> None:
        """在岛下方挂一条当前歌词；暂停时停在那一句，没有歌词/没有进度就收起。"""
        if not self.cfg.get("show_lyrics", True):
            self.lyric_bar.hide()
            return
        st = self.media.get_state()
        if (not st.has_session or not st.has_position):
            self.lyric_bar.hide()
            return
        lines = lyrics_mod.cached(st.title, st.artist)
        line = lyrics_mod.line_at(lines, st.live_position())
        if not line:
            self.lyric_bar.hide()
            return
        self.lyric_bar.set_text(line)
        # 岛收上去躲起来时歌词贴屏幕最上沿；岛露着时就挂在它下面
        if self._auto_hidden:
            self.lyric_bar.stick_top(self.x(), self.width(), self._screen_geo().top())
        else:
            self.lyric_bar.follow(self.frameGeometry())
        if not self.lyric_bar.isVisible():
            self.lyric_bar.show()

    def _on_scan(self, accounts, unread) -> None:
        """后台扫描线程的结果回到界面线程：只在这里重建控件、上红点。"""
        if accounts is None:
            if unread is not None:
                self._apply_unread(unread)
            return
        self._im_accounts = accounts
        self._apply_accounts(unread or {})

    def _apply_accounts(self, unread: dict) -> None:
        enabled = self.scanner.enabled()

        # 账号集合没变就不重建控件，避免每 2 秒闪烁
        sig = tuple((a.client_key, a.hwnd, a.title) for a in self._im_accounts)
        if sig == self._im_signature:
            self._apply_unread(unread)
            return
        self._im_signature = sig

        # 收起态：每个客户端一个图标（有账号才显示）
        self._clear_layout(self.coll_icons)
        self.coll_buttons = {}
        present = {a.client_key for a in self._im_accounts}
        for key in ("wechat", "qq", "wecom", "feishu", "dingtalk"):
            if enabled.get(key) and key in present:
                btn = ContactButton(key, next(
                    c.name for c in im.CLIENTS if c.key == key))
                btn.clicked.connect(lambda _=False, k=key: self._focus_by_client(k))
                self.coll_icons.addWidget(btn)
                self.coll_buttons[key] = btn

        # 展开态：逐条列出多开账号
        self._clear_layout(self.im_column)
        if not self._im_accounts:
            empty = QLabel("无登录的聊天窗口")
            empty.setStyleSheet(f"color:{SUBTEXT}; font-size:12px;")
            self.im_column.addWidget(empty)
            self._after_accounts(unread)
            return
        # 同一客户端多开时标题可能完全一样，按顺序加编号区分
        seen = {}
        for acc in self._im_accounts:
            seen[acc.client_key] = seen.get(acc.client_key, 0) + 1
            idx = seen[acc.client_key]
            same_client = sum(
                1 for a in self._im_accounts if a.client_key == acc.client_key)
            label = acc.client_name
            if same_client > 1:
                label = f"{acc.client_name}{idx}"
            state = ""
            btn = QPushButton(f"{label}{state}")
            btn.setFixedHeight(32)
            btn.setCursor(Qt.PointingHandCursor)
            hint = "（托盘" if acc.is_hidden else "（"
            btn.setToolTip(f"{acc.title}{hint}pid {acc.pid}）")
            color = CLIENT_COLORS.get(acc.client_key, "#8e8e93")
            btn.setStyleSheet(
                f"QPushButton{{color:{TEXT};background:#2c2c2e;border-radius:8px;"
                f"text-align:left;padding-left:10px;font-size:12px;border-left:3px solid {color};}}"
                f"QPushButton:hover{{background:#3a3a3c;}}"
            )
            btn.clicked.connect(lambda _=False, h=acc.hwnd: self._focus_account(h))
            self.im_column.addWidget(btn)

        # 多余的高度留在最下面，别把几行账号平均撑开（间隔看着像空了一大片）
        self.im_column.addStretch(1)

        # 重建图标后同步一次红点状态（红点由扫描线程算好带过来，不再就地采样）
        self._after_accounts(unread)

    def _after_accounts(self, unread: dict) -> None:
        self._apply_unread(unread)
        self._apply_blink()          # 图标刚重建，把闪烁状态重新套上去
        self._apply_collapsed_size()
        if self.expanded:
            self._force_size(*self._expanded_size())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 首次显示时布局还没量过，按真实内容宽度摆一次（否则新增图标会挤在一起）；
        # 字体/样式要几百毫秒才 polish 完，所以补两次，再由每秒的时钟兜底
        self._apply_collapsed_size()
        for _ms in (700, 1600):
            QTimer.singleShot(_ms, self._apply_collapsed_size)

    def _collapsed_width(self) -> int:
        """收起态宽度直接按内容算，不等布局 polish（一闪而过的 sizeHint 会算成 250）。"""
        margins = 14 + 16
        tool_n = len(self._selected_tools)
        tools = tool_n * 24 + max(0, tool_n - 1) * 3 if tool_n else 0
        gaps = 12 * 3                      # 工具栏 / 时间 / 智能体头像 之间
        time_w = self.coll_time.sizeHint().width() or 55
        avatar = self.btn_agent.width() or 38
        n = len(self.coll_buttons)
        clients = n * 30 + max(0, n - 1) * 8
        total = margins + tools + gaps + time_w + avatar + 6
        if n:
            total += 12 + clients
        return max(250, min(560, total))

    def _apply_collapsed_size(self) -> None:
        if self.expanded:
            return
        w = self._collapsed_width()
        if w != self.width() or self.height() != 56:
            center = self._screen_geo().center().x()
            self._force_size(w, 56)
            if self._anchor is not None:
                self.move(self._anchor.x(), self._anchor.y())
            elif self._auto_hidden:
                # 缩在屏幕顶的时候改宽度，别顺手把它弹出来
                self.move(center - w // 2, self.y())
            else:
                self.move(center - w // 2, self._screen_geo().top() + 8)
            return
        # 尺寸没变也要把位置摆正：靠边隐藏动画结束后偶尔会停在半空
        if (self._anchor is None and self._drag_pos is None
                and not self._auto_hidden and self._anim is None):
            x = self._screen_geo().center().x() - w // 2
            y = self._screen_geo().top() + 8
            if (self.x(), self.y()) != (x, y):
                self.move(x, y)

    def _focus_account(self, hwnd: int) -> None:
        acc = next((a for a in self._im_accounts if a.hwnd == hwnd), None)
        key = acc.client_key if acc else None
        # 收起放到事件循环的空转点：折叠会重建账号按钮，不能在它自己的
        # clicked 回调里把按钮删掉。
        QTimer.singleShot(0, self.collapse)
        # 切过去就当作已读，红点立即清掉
        if acc:
            self.unread.clear(acc.client_key)
        # 唤起可能要走任务栏/托盘入口，耗时 2-3 秒，放到后台线程，别冻住界面
        threading.Thread(target=self._raise_async, args=(hwnd, key), daemon=True).start()

    def _raise_async(self, hwnd: int, key) -> None:
        try:
            im.focus_account(hwnd, key)
        except Exception:
            pass
        QTimer.singleShot(0, self.refresh_poll)

    def _focus_by_client(self, key: str) -> None:
        matches = [a for a in self._im_accounts if a.client_key == key]
        if matches:
            self._focus_account(matches[0].hwnd)

    # ---------- 工具：剪贴板 / 截图 ----------
    def _tool_widgets(self, key: str) -> list:
        """同一个工具在胶囊和展开面板上的两个按钮，启用/隐藏要一起改。"""
        out = [self._tool_buttons[key]] if key in self._tool_buttons else []
        chip = getattr(self, "_exp_chips", {}).get(key)
        if chip is not None:
            out.append(chip)
        return out

    def attach_tools(self, clip_panel=None, clip_watcher=None, snip=None,
                     hint: str = "") -> None:
        self.clip_panel = clip_panel
        self.clip_watcher = clip_watcher
        self.snip = snip
        if self.incident_panel is not None:
            self.incident_panel.clip_watcher = clip_watcher
        for b in self._tool_widgets("clip"):
            b.setEnabled(clip_panel is not None)
        for b in self._tool_widgets("snip"):
            b.setEnabled(snip is not None)
        self.btn_snip.setToolTip(
            f"框选一块屏幕区域（{hint}）" if hint else "框选一块屏幕区域，复制并保存 PNG")
        self.snip_hint.setText(hint)
        self._apply_collapsed_size()

    def attach_reminders(self, center=None) -> None:
        self.reminder_center = center
        for key in ("bell", "list"):
            for b in self._tool_widgets(key):
                b.setEnabled(center is not None)
        if center is not None:
            center.badge_changed.connect(self.set_reminder_badge)
            center.due.connect(self._on_reminder_due)
            self.set_reminder_badge(center.store.overdue_count())
        self._apply_collapsed_size()

    def _on_reminder_due(self) -> None:
        """到点时岛躲着就自己出来一下，铃铛的琥珀色和红点才看得见。"""
        if self._auto_hidden:
            self._show_from_edge()
        self._leave_t = None

    def set_reminder_badge(self, count: int) -> None:
        """到点没处理的条数：铃铛点亮，收起态也看得见；列表键只做提示不另挂角标。"""
        self.btn_bell.set_alert(bool(count))

    def _toggle_popup(self, win, opener) -> bool:
        """点一次开、再点一次关；开之前先把别的卡片收掉，保证同一时刻只有一张。"""
        if win.isVisible():
            win.hide()
            return True
        self._dismiss_others(keep=win)
        opener()
        return True
    def open_reminder_new(self) -> None:
        center = self.reminder_center
        if center is None:
            return

        def _open() -> None:
            if self.clip_panel is not None:
                self.clip_panel.hide()
            self._hide_cheatsheet()
            if self._auto_hidden:
                self._show_from_edge()
            center.new_reminder()

        self._toggle_popup(center.dialog, _open)

    def open_reminder_list(self) -> None:
        center = self.reminder_center
        if center is None:
            return

        def _open() -> None:
            if self.clip_panel is not None:
                self.clip_panel.hide()
            self._hide_cheatsheet()
            if self._auto_hidden:
                self._show_from_edge()
            center.open_list()

        self._toggle_popup(center.list_panel, _open)

    def open_clipboard(self) -> None:
        if self.clip_panel is None:
            return

        def _open() -> None:
            if self._auto_hidden:
                self._show_from_edge()
            if self.reminder_center is not None:
                self.reminder_center.hide_all()
            self._hide_cheatsheet()
            self._hide_work_panels()
            self.clip_panel.popup_near(self.visible_rect())

        self._toggle_popup(self.clip_panel, _open)

    def open_bejson(self) -> None:
        QDesktopServices.openUrl(QUrl(BEJSON_URL))

    def open_cheatsheet(self) -> None:
        """Excel 快捷键大全：浮层里带「钉住」，钉住后点别处也不会收。"""
        from .cheatsheet import EXCEL_SHORTCUTS, CheatSheetPanel
        if self.cheatsheet is None:
            self.cheatsheet = CheatSheetPanel("Excel 快捷键", EXCEL_SHORTCUTS)

        def _open() -> None:
            if self.clip_panel is not None:
                self.clip_panel.hide()
            if self.reminder_center is not None:
                self.reminder_center.hide_all()
            if self._auto_hidden:
                self._show_from_edge()
            self._hide_work_panels()
            self.cheatsheet.popup_near(self.visible_rect())

        self._toggle_popup(self.cheatsheet, _open)

    def _hide_cheatsheet(self) -> None:
        """别的浮层要开了：没钉住的速查卡先收起来，钉住的留着。"""
        if self.cheatsheet is not None and not self.cheatsheet.pinned:
            self.cheatsheet.hide()

    # ---------- 工作面板 ----------
    def _ensure_project_panel(self):
        if self.project_panel is None:
            from .projects import ProjectPanel
            self.project_panel = ProjectPanel()
        return self.project_panel

    def _ensure_app_panel(self):
        if self.app_panel is None:
            from .apps import AppPanel
            self.app_panel = AppPanel()
            self.app_panel.projects_requested.connect(self.open_project_dirs)
        return self.app_panel

    def _ensure_incident_panel(self):
        if self.incident_panel is None:
            from .incident import IncidentPanel
            self.incident_panel = IncidentPanel(self.clip_watcher)
            self.incident_panel.copied.connect(self._notify)
        return self.incident_panel

    def _ensure_clerk_panel(self):
        if self.clerk_panel is None:
            from .clerk import ClerkPanel
            self.clerk_panel = ClerkPanel()
        return self.clerk_panel

    def _ensure_favorites_panel(self):
        if self.favorites_panel is None:
            from .favorites import FavoritesPanel
            self.favorites_panel = FavoritesPanel()
        return self.favorites_panel

    def open_favorites(self) -> None:
        """网站收藏：点胶囊图标打开列表，行内点击用默认浏览器跳转。"""
        panel = self._ensure_favorites_panel()
        self._open_work_panel(panel)

    def _show_tool_hover(self, name: str, anchor) -> None:
        if not name:
            self._hide_tool_hover()
            return
        if self._hover_tip is None:
            self._hover_tip = HoverTip()
        self._hover_tip.show_text(name, anchor)

    def _hide_tool_hover(self) -> None:
        if self._hover_tip is not None:
            self._hover_tip.hide_tip()

    def _notify(self, text: str) -> None:
        from .toast import Toast
        if self._toast is None:
            self._toast = Toast()
        self._toast.set_text(text)
        self._toast.show_at(self.visible_rect())

    def _hide_work_panels(self, keep=None) -> None:
        for win in (self.project_panel, self.app_panel, self.incident_panel,
                    self.clerk_panel, self.favorites_panel):
            if win is not None and win is not keep and win.isVisible():
                win.hide()

    def _open_work_panel(self, panel) -> None:
        if panel.isVisible():
            panel.hide()
            return
        self._dismiss_others(keep=panel)
        self._hide_work_panels(keep=panel)
        if self._auto_hidden:
            self._show_from_edge()
        self._leave_t = None
        panel.popup_near(self.visible_rect())

    def open_projects(self) -> None:
        """应用快开：默认打开开始菜单应用列表，不再让用户进目录找 exe。"""
        panel = self._ensure_app_panel()
        if not panel.isVisible():
            panel.reset_search()
        self._open_work_panel(panel)

    def open_project_dirs(self) -> None:
        panel = self._ensure_project_panel()
        if not panel.isVisible():
            panel.reload()
        self._open_work_panel(panel)

    def open_incident(self) -> None:
        panel = self._ensure_incident_panel()
        if not panel.isVisible():
            panel.refresh()
        self._open_work_panel(panel)

    def open_clerk(self) -> None:
        self._open_work_panel(self._ensure_clerk_panel())

    @staticmethod
    def _prewarm_card(panel) -> None:
        panel.setWindowOpacity(0.0)
        panel.show()
        QApplication.processEvents()   # 先把首次排版/绘制做掉，别留到用户点击
        panel.hide()
        QApplication.processEvents()
        panel.setWindowOpacity(1.0)

    def _prewarm_work_key(self, key: str, ensure) -> None:
        panel = ensure()
        if key == "incident":
            panel.refresh()          # 首开贵在 QPlainTextEdit 首次排版，先排一遍
        self._prewarm_card(panel)

    def prewarm_work_panels(self) -> None:
        """启动时只预热本次勾选的工作面板，别给没用的功能付启动时间。"""
        for key, ensure in (("proj", self._ensure_app_panel),
                            ("incident", self._ensure_incident_panel),
                            ("clerk", self._ensure_clerk_panel)):
            if key in self._selected_tools:
                self._prewarm_work_key(key, ensure)

    # ---------- 工具箱 ----------
    EXPANDED_TOOL_LIMIT = 3   # 展开面板下排最多再放几个工具（工具箱另算）

    def _apply_tool_visibility(self) -> None:
        """胶囊按全部勾选显示；展开面板下排只放前 3 个，避免挤成一团。

        下排顺序仍跟胶囊一致（工具箱在最前），其余工具在胶囊上点。
        """
        for key, btn in self._tool_buttons.items():
            btn.setVisible(key in self._selected_tools)
        expanded = self._selected_tools[:self.EXPANDED_TOOL_LIMIT]
        for key, btn in getattr(self, "_exp_chips", {}).items():
            btn.setVisible(key in expanded)

    def _hide_tool_popup(self, key: str) -> None:
        """某个工具被取消勾选：顺手把它开着的浮层收掉，别留孤儿窗口。"""
        if key == "clip" and self.clip_panel is not None:
            self.clip_panel.hide()
        elif key == "excel" and self.cheatsheet is not None:
            self.cheatsheet.hide()
        elif key in ("bell", "list") and self.reminder_center is not None:
            target = (self.reminder_center.dialog if key == "bell"
                      else self.reminder_center.list_panel)
            target.hide()
        elif key == "proj":
            if self.app_panel is not None:
                self.app_panel.hide()
            if self.project_panel is not None:
                self.project_panel.hide()
        elif key == "incident" and self.incident_panel is not None:
            self.incident_panel.hide()
        elif key == "clerk" and self.clerk_panel is not None:
            self.clerk_panel.hide()
        elif key == "fav" and self.favorites_panel is not None:
            self.favorites_panel.hide()

    def prewarm_toolbox(self) -> None:
        """启动时把工具箱窗口先建好并离屏显示一次。

        Qt/PySide 在本机第一次显示这套控件要 0.6～3 秒；放在启动阶段吃掉，
        用户点工具箱时才能立刻弹出。整个过程透明度为 0，不会闪一下。
        """
        if self.toolbox is not None:
            return
        from .toolbox import ToolboxPanel
        self.toolbox = ToolboxPanel(self._selected_tools)
        self.toolbox.changed.connect(self._on_tools_changed)
        self.toolbox.closed.connect(self._on_toolbox_closed)
        self.toolbox.setWindowOpacity(0.0)
        self.toolbox.show()
        self.toolbox.hide()
        self.toolbox.setWindowOpacity(1.0)

    def open_toolbox(self) -> None:
        """工具箱：勾选胶囊上显示哪些按钮；开着的时候胶囊不自动藏。"""
        from .toolbox import ToolboxPanel
        if self.toolbox is None:
            self.toolbox = ToolboxPanel(self._selected_tools)
            self.toolbox.changed.connect(self._on_tools_changed)
            self.toolbox.closed.connect(self._on_toolbox_closed)
        if self.toolbox.isVisible():
            self.toolbox.close_panel()
            return
        self._dismiss_others(keep=self.toolbox)
        if self._auto_hidden:
            self._show_from_edge()
        self._toolbox_open = True
        self._leave_t = None
        self.toolbox.set_selected(self._selected_tools)
        self.toolbox.popup_near(self.visible_rect())

    def _on_tools_changed(self, keys: list) -> None:
        from core import config as config_mod
        old = set(self._selected_tools)
        self._selected_tools = [k for k in keys if k in self._tool_buttons]
        self.cfg["capsule_tools"] = list(self._selected_tools)
        try:
            config_mod.save(self.cfg)
        except Exception:
            pass
        for key in old - set(self._selected_tools):
            self._hide_tool_popup(key)
        ensure = {"proj": self._ensure_app_panel,
                  "incident": self._ensure_incident_panel,
                  "clerk": self._ensure_clerk_panel}
        for key in set(self._selected_tools) - old:
            fn = ensure.get(key)
            if fn is not None:
                QTimer.singleShot(0, lambda k=key, f=fn: self._prewarm_work_key(k, f))
        self._apply_tool_visibility()
        self._apply_collapsed_size()
        if self.toolbox is not None and self.toolbox.isVisible():
            self.toolbox.popup_near(self.visible_rect())

    def _on_toolbox_closed(self) -> None:
        self._toolbox_open = False
        self._leave_t = time.monotonic() + 2.0     # 关掉后留两秒再缩，给个缓冲

    def start_snip(self) -> None:
        if self.snip is None:
            return
        self.snip.start()

    def hide_for_snip(self) -> None:
        """截图前把岛和歌词条让开，别把自己截进去。"""
        if self._runner is not None:
            try:
                self._runner.close()
            except Exception:
                pass
        self._visible_before_snip = self.isVisible()
        try:
            self.lyric_bar.hide()
        except Exception:
            pass
        if self.reminder_center is not None:
            # 提醒弹层也一起收，不然会被冻进截图里
            self.reminder_center.hide_all()
        self._dismiss_others()
        if self.cheatsheet is not None:
            self.cheatsheet.hide()
        if self.toolbox is not None and self.toolbox.isVisible():
            # 工具箱也要收，否则会被冻进截图；顺手解除"不自动收起"的挂起状态
            self.toolbox.close_panel()
        self.hide()

    def restore_after_snip(self) -> None:
        if not self._visible_before_snip:
            return
        self.show()
        if self._auto_hidden:
            self._show_from_edge()

    # ---------- 未读 ----------
    def _apply_unread(self, state: dict) -> None:
        now = {k: bool(v) for k, v in (state or {}).items()}
        for key, btn in self.coll_buttons.items():
            btn.set_unread(now.get(key, False))

        # 刚变成未读的客户端：进闪烁集合；已经读掉的移出
        fresh = [k for k, v in now.items() if v and not self._unread_prev.get(k, False)]
        changed = False
        for key, unread in now.items():
            if not unread and key in self._blink_keys:
                self._blink_keys.discard(key)
                changed = True
        if fresh:
            self._blink_keys.update(fresh)
            self._blink_on = True
            # 岛正躲着就自己滑出来，让人一眼能看到是谁来消息了
            if self._auto_hidden:
                self._show_from_edge()
            changed = True
        if changed:
            self._apply_blink()

        # 分不清哪些客户端还在：这次没给状态的一律清掉闪烁，别让岛一直赖着
        for key in list(self._blink_keys):
            if key not in now:
                self._blink_keys.discard(key)
                changed = True

        # 有未读期间不许自己缩回屏幕顶，直到消息被读掉
        if any(now.values()):
            self._leave_t = None

        self._unread_prev = now

    def _blink_tick(self) -> None:
        if not self._blink_keys:
            if self._blink_on:
                self._blink_on = False
                self._apply_blink()
            return
        self._blink_on = not self._blink_on
        self._apply_blink()

    def _apply_blink(self) -> None:
        for key, btn in self.coll_buttons.items():
            btn.set_attention(self._blink_on and key in self._blink_keys)

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                # 先断父级再销毁，否则布局移除后控件仍会画在旧位置
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                IslandBar._clear_layout(item.layout())

