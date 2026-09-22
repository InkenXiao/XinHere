"""用户维度统计 · 我的任务看板 + 数据看板（全部按当前用户过滤）

- 任务看板：派发给我的 / 我派发的任务完成情况、AI 生成文档、保存的会议、token 消耗
- 数据看板：自定义业务能力使用、知识库、应用使用数据
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import BigInteger, func, select
from sqlalchemy.orm import Session

from ..persistence.models import (
    BizTask,
    BizTodo,
    HeroCard,
    KbSource,
    PlatformOperationLog,
    PlatformSession,
    PlatformSessionEvent,
    TaskRecord,
)

# 视为未办结的待办状态（逾期统计口径）
_OVERDUE_STATUSES = ("pending", "na_pending", "feedback_submitted", "submitted")


def user_overview(db: Session, user_id: str) -> dict:
    return {"task_board": _task_board(db, user_id), "data_board": _data_board(db, user_id)}


def _todo_stats(status_rows: dict, overdue: int) -> dict:
    total = sum(int(c) for c in status_rows.values())
    done = int(status_rows.get("completed", 0))
    return {"total": total, "done": done, "open": total - done, "overdue": int(overdue)}


def _task_board(db: Session, user_id: str) -> dict:
    # 派发给我的待办：按状态分组 + 逾期
    a_rows = dict(db.execute(
        select(BizTodo.status, func.count())
        .where(BizTodo.assignee_id == user_id)
        .group_by(BizTodo.status)
    ).all())
    a_overdue = db.scalar(
        select(func.count()).select_from(BizTodo).where(
            BizTodo.assignee_id == user_id,
            BizTodo.due < func.now(),
            BizTodo.status.in_(_OVERDUE_STATUSES),
        )
    ) or 0
    assigned = _todo_stats(a_rows, int(a_overdue))
    assigned_funnel = [{"status": s, "count": int(c)} for s, c in sorted(a_rows.items())]

    # 我派发的任务：我发起的任务单下所有待办的完成情况
    d_rows = dict(db.execute(
        select(BizTodo.status, func.count())
        .join(BizTask, BizTodo.task_id == BizTask.task_id)
        .where(BizTask.dispatcher_id == user_id)
        .group_by(BizTodo.status)
    ).all())
    d_tasks = db.scalar(
        select(func.count()).select_from(BizTask).where(BizTask.dispatcher_id == user_id)
    ) or 0
    dispatched = {**_todo_stats(d_rows, 0), "tasks": int(d_tasks)}

    # AI 生成文档：技能执行成功记录（会议纪要除外）
    docs = db.scalar(
        select(func.count()).select_from(TaskRecord).where(
            TaskRecord.user_id == user_id,
            TaskRecord.kind == "exec",
            TaskRecord.status == "success",
            func.coalesce(TaskRecord.ref_id, "") != "minutes",
        )
    ) or 0

    # 保存的会议：保存成功的会议纪要留痕（minutes/save 写入操作日志）
    meetings = db.scalar(
        select(func.count()).select_from(PlatformOperationLog).where(
            PlatformOperationLog.user_id == user_id,
            PlatformOperationLog.entity == "xuanpu_meeting",
            PlatformOperationLog.operation == "insert",
        )
    ) or 0

    # token 消耗：assistant/message 事件 usage（prompt + completion）累加
    data = PlatformSessionEvent.data
    token_expr = (
        func.coalesce(data["usage"]["prompt"].astext.cast(BigInteger), 0)
        + func.coalesce(data["usage"]["completion"].astext.cast(BigInteger), 0)
    )
    tokens = db.scalar(
        select(func.coalesce(func.sum(token_expr), 0))
        .select_from(PlatformSessionEvent)
        .join(PlatformSession, PlatformSession.session_id == PlatformSessionEvent.session_id)
        .where(PlatformSession.user_id == user_id, PlatformSessionEvent.type == "assistant/message")
    ) or 0

    # 近 14 天我的待办趋势
    today = date.today()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    created_rows = dict(db.execute(
        select(func.date(BizTodo.created_at), func.count())
        .where(BizTodo.assignee_id == user_id)
        .group_by(func.date(BizTodo.created_at))
    ).all())
    completed_rows = dict(db.execute(
        select(func.date(BizTodo.updated_at), func.count())
        .where(BizTodo.assignee_id == user_id, BizTodo.status == "completed")
        .group_by(func.date(BizTodo.updated_at))
    ).all())
    trend = [
        {"date": d.isoformat(), "created": int(created_rows.get(d, 0)), "completed": int(completed_rows.get(d, 0))}
        for d in days
    ]

    return {
        "assigned": assigned,
        "dispatched": dispatched,
        "docs": int(docs),
        "meetings": int(meetings),
        "tokens": int(tokens),
        "assigned_funnel": assigned_funnel,
        "trend_14d": trend,
    }


def _data_board(db: Session, user_id: str) -> dict:
    # 自定义业务能力（我的卡片）及使用次数
    cards = db.scalars(
        select(HeroCard).where(HeroCard.user_id == user_id).order_by(HeroCard.sort)
    ).all()
    run_rows = dict(db.execute(
        select(TaskRecord.ref_id, func.count())
        .where(TaskRecord.user_id == user_id, TaskRecord.kind == "exec",
               TaskRecord.ref_id.isnot(None))
        .group_by(TaskRecord.ref_id)
    ).all())
    custom_cards = [
        {"id": c.id, "name": c.name, "kind": c.kind, "runs": int(run_rows.get(c.id, 0))}
        for c in cards
    ]

    # 知识库：按类型计数
    kb_rows = dict(db.execute(select(KbSource.kb_type, func.count()).group_by(KbSource.kb_type)).all())
    kb = {
        "internal": int(kb_rows.get("internal", 0)),
        "external": int(kb_rows.get("external", 0)),
        "total": sum(int(v) for v in kb_rows.values()),
    }

    # 应用使用：会话 / 消息 / 执行次数 / 活跃天数
    sessions_cnt = db.scalar(
        select(func.count()).select_from(PlatformSession).where(PlatformSession.user_id == user_id)
    ) or 0
    messages_cnt = db.scalar(
        select(func.count())
        .select_from(PlatformSessionEvent)
        .join(PlatformSession, PlatformSession.session_id == PlatformSessionEvent.session_id)
        .where(PlatformSession.user_id == user_id, PlatformSessionEvent.type == "user/message")
    ) or 0
    exec_runs = db.scalar(
        select(func.count()).select_from(TaskRecord).where(
            TaskRecord.user_id == user_id, TaskRecord.kind == "exec"
        )
    ) or 0
    active_days = db.scalar(
        select(func.count(func.distinct(func.date(PlatformSession.created_at))))
        .where(PlatformSession.user_id == user_id)
    ) or 0

    return {
        "custom_cards": custom_cards,
        "kb": kb,
        "app_usage": {
            "sessions": int(sessions_cnt),
            "messages": int(messages_cnt),
            "exec_runs": int(exec_runs),
            "active_days": int(active_days),
        },
    }
