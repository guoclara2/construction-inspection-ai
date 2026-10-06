# 时间工具：统一以带时区 UTC 存储，输出按 APP_TIMEZONE 转换（消灭 naive 本地时间 D9）
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..config import settings


def now_utc() -> datetime:
    """当前时间（带 UTC 时区）。全代码库禁止再用 datetime.now() 作为时间基准。"""
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """把 datetime 规整为带 UTC 时区。naive 值按 UTC 解释。

    用于比较运算：SQLite 会把 tz-aware 列以 naive 形式取回，PostgreSQL 取回 aware，
    直接比较会因 naive/aware 混用报错，统一先经此函数规整。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_local(dt: datetime | None) -> datetime | None:
    """把任意 datetime 转为项目输出时区；naive 值按 UTC 解释以兼容历史数据。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(settings.APP_TIMEZONE))


def fmt(dt: datetime | None) -> str | None:
    """统一输出格式 YYYY-MM-DD HH:MM:SS（本地时区）。"""
    local = to_local(dt)
    return local.strftime("%Y-%m-%d %H:%M:%S") if local else None
