"""小二入口：启动桌面上那条悬浮条、托盘菜单、自启开关。"""
from __future__ import annotations

import sys
import traceback

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu

from island.bar import IslandBar
from island.branding import app_icon
from island.clipboard import ClipboardPanel, ClipboardWatcher
from island.reminders_ui import ReminderCenter
from island.snip import SnipSession
from core import config
from core.clipstore import ClipStore
from core.hotkey import GlobalHotkey
from core.media import MediaController
from core.reminders import ReminderStore


def _log_error() -> None:
    """把崩溃堆栈写入本地日志，便于排查启动失败。"""
    try:
        path = config.config_dir() / "error.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
    except OSError:
        pass


def main() -> int:
    try:
        return _run()
    except Exception:
        _log_error()
        raise


def _run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("小二")
    app.setApplicationDisplayName("小二")
    app.setQuitOnLastWindowClosed(False)
    icon = app_icon()
    app.setWindowIcon(icon)

    # 只允许一个实例：多开会让提醒各弹一遍，外观也完全重叠看不出来
    instance_key = "Xiaoer-IslandBar-v1"
    probe = QLocalSocket()
    probe.connectToServer(instance_key)
    if probe.waitForConnected(400):
        probe.write(b"show")
        probe.flush()
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        return 0
    QLocalServer.removeServer(instance_key)
    server = QLocalServer()
    server.listen(instance_key)

    cfg = config.load()
    config.migrate_autostart()
    media = MediaController()
    media.start()

    bar = IslandBar(cfg, media)
    bar.prewarm_toolbox()      # 首开成本挪到启动阶段，点工具箱即时弹出
    bar.prewarm_work_panels()  # 已勾选的工作面板同样先离屏建好
    bar.show()
    app.aboutToQuit.connect(bar.shutdown)

    def _on_second_launch() -> None:
        # 第二次双击 exe：不新开条，把已有的唤出来
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            if conn is not None:
                conn.readAll()
                conn.disconnectFromServer()
        bar.present()

    server.newConnection.connect(_on_second_launch)

    # 剪贴板历史 + 区域截图：两个都是纯本地工具，不联网、不存图片
    store = ClipStore(limit=int(cfg.get("clip_limit", 30) or 30))
    watcher = ClipboardWatcher(store, bool(cfg.get("clip_enabled", True)),
                               bool(cfg.get("clip_image", True)))
    clip_panel = ClipboardPanel(store, watcher)
    snip_dir = str(cfg.get("snip_dir", "") or "").strip()
    snip = SnipSession(save_dir=snip_dir or None,
                       before_grab=bar.hide_for_snip,
                       after_grab=bar.restore_after_snip,
                       confirm=lambda: bool(cfg.get("snip_confirm", False)))
    hotkey = GlobalHotkey(str(cfg.get("snip_hotkey", "ctrl+alt+x") or ""))
    hotkey_ok = hotkey.register()
    hotkey.triggered.connect(snip.start)
    bar.attach_tools(clip_panel, watcher, snip,
                     hint=(hotkey.spec.upper() if hotkey_ok else ""))

    # 提醒：胶囊铃铛建、列表看；到点弹内容泡，截图也能直接转成提醒
    reminder_store = ReminderStore()
    reminders = ReminderCenter(reminder_store, snip=snip,
                               anchor_provider=bar.visible_rect)
    bar.attach_reminders(reminders)
    snip.reminded.connect(reminders.new_from_image)
    reminders.check()

    # 桌面小伙伴先停用：胶囊头像只显示占位动画，不建小人窗口，
    # 也不再接收照片。后续接入用户自有模型时再从这里恢复。

    def _quit() -> None:
        hotkey.unregister()
        store.save()
        media.stop()
        app.quit()

    tray = QSystemTrayIcon(icon)
    tray.setToolTip("小二")
    menu = QMenu()

    action_toggle = QAction("展开 / 收起", menu)
    action_toggle.triggered.connect(bar.toggle)
    menu.addAction(action_toggle)

    action_autostart = QAction("开机自启", menu)
    action_autostart.setCheckable(True)
    action_autostart.setChecked(config.is_autostart())
    action_autostart.triggered.connect(lambda checked: (
        config.set_autostart(checked),
        cfg.update(autostart=checked),
        config.save(cfg),
    ))
    menu.addAction(action_autostart)

    action_edge = QAction("靠边自动隐藏", menu)
    action_edge.setCheckable(True)
    action_edge.setChecked(bool(cfg.get("edge_hide", True)))
    action_edge.triggered.connect(lambda checked: (
        bar.set_edge_hide(checked),
        cfg.update(edge_hide=checked),
        config.save(cfg),
    ))
    menu.addAction(action_edge)

    # 展开后闲置自动收起：秒数直接写在菜单里，0 = 关闭
    idle_menu = QMenu("展开后自动收起", menu)
    idle_group = QActionGroup(idle_menu)
    idle_group.setExclusive(True)
    current_idle = int(cfg.get("expanded_idle_sec", 30) or 0)

    def _set_idle(sec: int) -> None:
        bar.set_idle_collapse(sec)
        cfg.update(expanded_idle_sec=sec)
        config.save(cfg)

    for label, sec in (("关闭", 0), ("15 秒", 15), ("30 秒", 30),
                       ("60 秒", 60), ("120 秒", 120)):
        act = QAction(label, idle_menu)
        act.setCheckable(True)
        act.setChecked(current_idle == sec)
        act.triggered.connect(lambda checked, s=sec: _set_idle(s) if checked else None)
        idle_group.addAction(act)
        idle_menu.addAction(act)
    menu.addMenu(idle_menu)

    action_snip_confirm = QAction("截图后确认", menu)
    action_snip_confirm.setCheckable(True)
    action_snip_confirm.setChecked(bool(cfg.get("snip_confirm", False)))
    action_snip_confirm.triggered.connect(lambda checked: (
        cfg.update(snip_confirm=checked),
        config.save(cfg),
    ))
    menu.addAction(action_snip_confirm)

    action_lyrics = QAction("显示歌词", menu)
    action_lyrics.setCheckable(True)
    action_lyrics.setChecked(bool(cfg.get("show_lyrics", True)))
    action_lyrics.triggered.connect(lambda checked: (
        bar.set_show_lyrics(checked),
        cfg.update(show_lyrics=checked),
        config.save(cfg),
    ))
    menu.addAction(action_lyrics)

    menu.addSeparator()
    action_clip = QAction("剪贴板历史", menu)
    action_clip.triggered.connect(bar.open_clipboard)
    menu.addAction(action_clip)

    action_snip = QAction(
        f"区域截图（{hotkey.spec.upper()}）" if hotkey_ok else "区域截图", menu)
    action_snip.triggered.connect(bar.start_snip)
    menu.addAction(action_snip)

    action_remind = QAction("新建提醒", menu)
    action_remind.triggered.connect(bar.open_reminder_new)
    menu.addAction(action_remind)

    action_remind_list = QAction("提醒列表", menu)
    action_remind_list.triggered.connect(bar.open_reminder_list)
    menu.addAction(action_remind_list)

    menu.addSeparator()
    action_quit = QAction("退出", menu)
    action_quit.triggered.connect(_quit)
    menu.addAction(action_quit)

    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: bar.toggle() if reason == QSystemTrayIcon.Trigger else None
    )
    tray.show()

    code = app.exec()
    media.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
