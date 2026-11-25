"""本机系统状态：电量、时间日期、大小写锁定。全部走本地接口，不联网。"""
from __future__ import annotations

import ctypes
from datetime import datetime
from typing import Optional, Tuple

try:
    import psutil
except ImportError:  # 依赖缺失时降级，不让界面崩
    psutil = None

_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

VK_CAPITAL = 0x14


def battery() -> Tuple[Optional[float], bool]:
    """返回 (电量百分比 0-100, 是否接电源)；无电池返回 (None, False)。"""
    if psutil is None:
        return None, False
    try:
        s = psutil.sensors_battery()
        if s is None:
            return None, False
        return round(s.percent, 0), bool(s.power_plugged)
    except Exception:
        return None, False


def clock() -> Tuple[str, str, str]:
    """返回 (HH:MM, M月D日, 周X)。"""
    now = datetime.now()
    hhmm = now.strftime("%H:%M")
    date = f"{now.month}月{now.day}日"
    return hhmm, date, _WEEKDAY_CN[now.weekday()]


def caps_lock_on() -> bool:
    state = ctypes.windll.user32.GetKeyState(VK_CAPITAL)
    return bool(state & 1)
