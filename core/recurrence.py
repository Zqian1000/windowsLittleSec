"""周期提醒的日期推进：只算下一次什么时候，不碰存储和界面。

支持四种最常用的周期：
- daily    每天同一时刻
- weekdays 工作日（周一至周五）同一时刻
- weekly   每周同一天同一时刻
- monthly  每月同一日同一时刻（遇到短月自动落到月末）

所有计算都用本地时区，返回 epoch 秒；`none` 表示不重复。
"""
from __future__ import annotations

import calendar
import time
from datetime import datetime, timedelta

NONE = "none"
DAILY = "daily"
WEEKDAYS = "weekdays"
WEEKLY = "weekly"
MONTHLY = "monthly"

RULES = (NONE, DAILY, WEEKDAYS, WEEKLY, MONTHLY)
LABELS = {
    NONE: "不重复",
    DAILY: "每天",
    WEEKDAYS: "工作日",
    WEEKLY: "每周",
    MONTHLY: "每月",
}


def normalize(rule: str | None) -> str:
    rule = (rule or NONE).strip().lower()
    return rule if rule in RULES else NONE


def label(rule: str | None) -> str:
    return LABELS.get(normalize(rule), LABELS[NONE])


def _month_add(dt: datetime, months: int = 1) -> datetime:
    y = dt.year + (dt.month - 1 + months) // 12
    m = (dt.month - 1 + months) % 12 + 1
    day = min(dt.day, calendar.monthrange(y, m)[1])
    return dt.replace(year=y, month=m, day=day)


def next_due(base_ts: float, rule: str, now: float | None = None) -> float | None:
    """从 `base_ts` 这个计划点往后找第一个晚于 now 的时刻。

    连续错过好几次（比如电脑关了一周）时直接跳到未来的那一次，
    不会把已经过期的每一条都补弹一遍。
    """
    rule = normalize(rule)
    if rule == NONE:
        return None
    now = time.time() if now is None else float(now)
    dt = datetime.fromtimestamp(float(base_ts))
    if rule == MONTHLY:
        # 从原始日期按月加，不能在上一次结果上继续加：
        # 1/31 -> 2/28 若继续加会永远锁死在 28 号，3 月回不到 31 号。
        for i in range(1, 601):
            cand = _month_add(dt, i)
            if cand.timestamp() > now:
                return cand.timestamp()
        return None
    for _ in range(5000):
        if rule == DAILY:
            dt = dt + timedelta(days=1)
        elif rule == WEEKDAYS:
            dt = dt + timedelta(days=1)
            while dt.weekday() >= 5:
                dt = dt + timedelta(days=1)
        elif rule == WEEKLY:
            dt = dt + timedelta(days=7)
        else:
            return None
        ts = dt.timestamp()
        if ts > now:
            return ts
    return None
