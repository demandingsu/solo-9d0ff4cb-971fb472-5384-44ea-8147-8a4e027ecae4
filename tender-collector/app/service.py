"""采集编排服务：抓取 -> 归并校验 -> 单事务落库；失败时保留上次成功结果。"""
from __future__ import annotations

from datetime import datetime

from . import database
from .merge import EffectiveDeadline, MergeError, merge_projects
from .scraper import CollectionError, collect
from .times import TimeParseError, as_instant, now_instant, parse_instant


class ServiceError(Exception):
    """对外统一的采集错误（带可读原因）。"""


def _is_expired(deadline_text: str, now: datetime | None = None) -> bool:
    """截止时间是否已过。

    无时区值按北京时间解释，带偏移值按实际时刻比较；与服务器本地时区无关。
    时间无法解释时抛 TimeParseError（采集流程中转为采集错误）。
    """
    deadline = parse_instant(deadline_text)
    current = as_instant(now) if now is not None else now_instant()
    return deadline <= current


def _to_project(d: EffectiveDeadline, now: datetime | None = None) -> dict:
    return {
        "project_name": d.project_name,
        "current_deadline": d.deadline,  # 公告原文时间文本
        "deadline_source_code": d.source_code,
        "deadline_source_url": d.source_url,
        "is_expired": _is_expired(d.deadline, now),
    }


def build_projects(rows: list[dict], now: datetime | None = None) -> list[dict]:
    return [_to_project(d, now) for d in merge_projects(rows)]


def run_collection(list_url: str) -> dict:
    started_at = database.utcnow_iso()
    try:
        raw = collect(list_url)
        # 先在内存完成归并校验（含全部时间字段解析）：数据有问题时不写库
        projects = build_projects(raw)
    except (CollectionError, MergeError, TimeParseError) as exc:
        # 记录失败采集；announcements 表完全不动，保留上次成功结果
        with database.get_conn() as conn:
            database.record_run(
                conn,
                source_url=list_url,
                status="failed",
                items_collected=0,
                error=str(exc),
                started_at=started_at,
            )
            conn.commit()
        raise ServiceError(str(exc)) from exc

    # 成功：单事务 UPSERT + 运行记录，保证原子性
    with database.get_conn() as conn:
        try:
            database.upsert_announcements(conn, raw)
            database.record_run(
                conn,
                source_url=list_url,
                status="success",
                items_collected=len(raw),
                error=None,
                started_at=started_at,
            )
            conn.commit()
        except Exception as exc:  # pragma: no cover - 兜底回滚
            conn.rollback()
            raise ServiceError(f"写入数据库失败: {exc}") from exc

    return {"source_url": list_url, "collected": len(raw), "projects": projects}


def query_projects(now: datetime | None = None) -> list[dict]:
    with database.get_conn() as conn:
        rows = [dict(r) for r in database.fetch_all_announcements(conn)]
    if not rows:
        return []
    return build_projects(rows, now)
