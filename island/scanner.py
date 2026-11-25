"""后台扫描线程：窗口枚举、红点采样本该在后台做，别卡住绘制和拖动。

以前这两件事都挂在界面线程的定时器上：账号枚举 2 秒一次、红点采样 0.3 秒
一次，其中红点采样偶尔会摸到任务栏 UIA（实测单次峰值 200ms 以上），
慢一点的机器上就会变成"岛动一下顿一下"。现在统一丢到工作线程，
算完把结果用信号送回界面线程，界面只负责重画。
"""
from __future__ import annotations

import ctypes
import time

from PySide6.QtCore import QThread, Signal

import core.im as im

try:
    import win32process
except Exception:  # pragma: no cover - 非 Windows
    win32process = None


class ScannerWorker(QThread):
    # (accounts 或 None, unread 字典 或 None)
    scanned = Signal(object, object)

    BASE_ACCOUNTS_MS = 2000       # 窗口枚举的基础间隔
    BASE_UNREAD_MS = 300          # 红点采样基础节拍
    MAX_ACCOUNTS_MS = 4000        # 枚举最多放慢到 4s（再慢展开面板就明显迟滞）
    MAX_UNREAD_MS = 600           # 采样最多放慢到 0.6s（再慢就追不上系统托盘的红点）

    def __init__(self, cfg: dict, watcher, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.watcher = watcher
        self.accounts_ms = self.BASE_ACCOUNTS_MS
        self.unread_ms = self.BASE_UNREAD_MS
        self.last_round_ms = 0.0
        self.last_enum_ms = 0.0
        self.last_cycle_ms = 0.0
        self.last_emit_ms = 0.0
        self.last_sleep_ms = 0.0
        self._prev_t0 = 0.0
        self._stop = False
        self._accounts_t = 0.0
        self._good_rounds = 0

    # ---- 供界面线程读取 ----

    def kick(self) -> None:
        """让下一轮立刻重新枚举窗口（展开、点过图标、刚启动时用）。"""
        self._accounts_t = 0.0

    def slow_down(self, factor: float = 1.4) -> None:
        """只用于外部显式放慢；各自有上限，且会在快轮次里自动恢复。"""
        self.accounts_ms = min(self.MAX_ACCOUNTS_MS,
                               int(self.accounts_ms * factor))
        self.unread_ms = min(self.MAX_UNREAD_MS,
                             int(self.unread_ms * factor))

    # ---- 线程体 ----

    def enabled(self) -> dict:
        return {
            "wechat": self.cfg.get("wechat_enabled", True),
            "qq": self.cfg.get("qq_enabled", True),
            "wecom": self.cfg.get("wecom_enabled", True),
            "feishu": self.cfg.get("feishu_enabled", True),
            "dingtalk": self.cfg.get("dingtalk_enabled", True),
        }

    def run(self) -> None:                  # pragma: no cover - 线程
        # 工作线程里要摸 UIA/COM，先给本线程初始化一遍
        ole32 = ctypes.windll.ole32
        try:
            ole32.CoInitialize(None)
        except Exception:
            pass
        try:
            cached: list = []
            rounds_total = 0
            while not self._stop:
                t0 = time.monotonic()
                if self._prev_t0:
                    self.last_cycle_ms = (t0 - self._prev_t0) * 1000.0
                self._prev_t0 = t0
                enabled = self.enabled()
                fresh = None
                enum_cost = 0.0
                if t0 - self._accounts_t >= self.accounts_ms / 1000.0:
                    te = time.monotonic()
                    fresh = im.list_accounts(enabled)
                    enum_cost = (time.monotonic() - te) * 1000.0
                    self.last_enum_ms = enum_cost
                    cached = fresh
                    self._accounts_t = t0
                # 红点每拍都算，用最近一次枚举到的窗口做底；不能因为这一拍
                # 没重新枚举就发空字典，否则界面上的红点会一闪一闪。
                unread = {}
                t_poll = time.monotonic()
                if cached:
                    fg = 0
                    try:
                        fg = im.foreground_root()
                    except Exception:
                        fg = 0
                    # 按进程判定“正在用这个客户端”，不要求前台窗口正好是主窗：
                    # 微信/飞书点开后会弹子窗口，前台句柄和主窗口对不上，
                    # 旧写法会以为人没在看，红点清了过会儿又冒出来。
                    fg_pid = 0
                    if fg and win32process is not None:
                        try:
                            fg_pid = win32process.GetWindowThreadProcessId(fg)[1]
                        except Exception:
                            fg_pid = 0
                    foreground = {a.client_key for a in cached
                                  if fg_pid and a.pid == fg_pid}
                    active: dict = {}
                    for acc in cached:
                        active[acc.client_key] = (
                            active.get(acc.client_key, False) or not acc.is_hidden)
                    try:
                        unread = self.watcher.poll(enabled, foreground, active)
                    except Exception:
                        unread = {}
                poll_cost = (time.monotonic() - t_poll) * 1000.0
                cost = (time.monotonic() - t0) * 1000.0
                # 日志记真正拖慢红点的那一段，枚举耗时单独看 enum_cost
                self.last_round_ms = poll_cost
                rounds_total += 1
                t_emit = time.monotonic()
                if not self._stop:
                    self.scanned.emit(fresh, unread)
                self.last_emit_ms = (time.monotonic() - t_emit) * 1000.0
                # 自适应：哪一路慢就只放慢哪一路，别把另一路一起拖下水；
                # 连续快轮次再逐步收回基础节拍，不然一次卡顿会永久停在最慢。
                self._adapt_pacing(poll_cost, enum_cost, rounds_total)
                t_sleep = time.monotonic()
                self.msleep(max(30, self.unread_ms - int(cost)))
                self.last_sleep_ms = (time.monotonic() - t_sleep) * 1000.0
        finally:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass

    def _adapt_pacing(self, poll_cost: float, enum_cost: float,
                      rounds_total: int) -> None:
        """按两路各自的耗时调速，并在连续快轮次里把节拍收回来。"""
        if rounds_total <= 3:
            return
        if enum_cost > max(200.0, self.accounts_ms * 0.5):
            self.accounts_ms = min(
                self.MAX_ACCOUNTS_MS,
                max(self.BASE_ACCOUNTS_MS, int(self.accounts_ms * 1.25)))
            self._good_rounds = 0
        if poll_cost > max(120.0, self.unread_ms * 0.6):
            self.unread_ms = min(
                self.MAX_UNREAD_MS,
                max(self.BASE_UNREAD_MS, int(self.unread_ms * 1.35)))
            self._good_rounds = 0
        elif poll_cost < max(60.0, self.unread_ms * 0.35):
            self._good_rounds += 1
            if self._good_rounds >= 10:
                self.unread_ms = max(self.BASE_UNREAD_MS,
                                     int(self.unread_ms * 0.8))
                self.accounts_ms = max(self.BASE_ACCOUNTS_MS,
                                       int(self.accounts_ms * 0.85))
                self._good_rounds = 0

    def stop(self) -> None:
        self._stop = True
        self.wait(2000)
