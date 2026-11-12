"""公告时间的统一解析与比较。

同站公告的时间字段混用两种写法：
- 无时区的本地时间（如 "2026-11-03 11:20"）—— 一律按北京时间（UTC+8）解释；
- 带偏移的 ISO 时间（如 "2026-11-03T03:20:00+00:00"）—— 按自身偏移解释。

两种写法一律换算为带时区的实际时刻后再比较/排序，结果与服务器本地时区无关。
无法解释的值抛出 TimeParseError，由上层转为采集错误（保留上次成功结果）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# 北京时间（东八区，无夏令时）
BEIJING_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


class TimeParseError(ValueError):
    """时间字段无法解释。"""


# fromisoformat 之外的兜底格式
_FALLBACK_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M %z",
    "%Y-%m-%d %H:%M:%S %z",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
)


def parse_instant(text: str | None) -> datetime:
    """解析公告时间文本，返回可相互比较的实际时刻（带时区）。

    无时区值按北京时间解释；带偏移值保留自身偏移。
    """
    if text is None:
        raise TimeParseError("时间字段缺失")
    t = text.strip()
    if not t:
        raise TimeParseError("时间字段为空")
    if t.endswith(("Z", "z")):  # UTC 的 ISO 简写
        t = t[:-1] + "+00:00"

    dt: datetime | None = None
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        for fmt in _FALLBACK_FORMATS:
            try:
                dt = datetime.strptime(t, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        raise TimeParseError(f"无法识别的时间格式: {text!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING_TZ)
    return dt


def as_instant(dt: datetime) -> datetime:
    """把（可能无时区的）datetime 统一为可比较时刻；无时区按北京时间解释。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BEIJING_TZ)
    return dt


def now_instant() -> datetime:
    """当前实际时刻（UTC），与服务器本地时区无关。"""
    return datetime.now(timezone.utc)
