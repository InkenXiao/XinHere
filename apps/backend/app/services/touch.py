"""触达与主动服务（P5）：今日工作要点聚合 + 每日晨间扫描 daemon。

- daily_brief：处理人侧（逾期/今日到期/被退回）+ 派发人侧（待审/不涉及待确认/多日未反馈可催）
  聚合为今日要点（v1 规则排序：逾期 > 今日到期 > 被退回 > 待审 > 不涉及确认；LLM 排序后置）。
- 每日扫描：daemon 线程晨间定点扫描，向在线用户（todo_bus 订阅者）推 brief/ready；
  离线用户下次首屏拉 /todos/brief 兜底。参照 report.py 的 daemon 线程模式。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.models import BizTask, BizTodo
from ..persistence.session import SessionLocal
from ..platform.events.todo_bus import online_user_ids, publish
from .todo import _display_name, derive_overdue, period_locked

logger = logging.getLogger(__name__)

SCAN_HOUR = 9  # 晨间扫描点（本地时区）


def _fmt_due(t: BizTodo) -> str | None:
    return t.due.date().isoformat() if t.due else None


def daily_brief(db: Session, user_id: str, today: datetime | None = None) -> dict:
    today = today or datetime.now(timezone.utc)
    today = today.astimezone()
    items: list[dict] = []

    def add(t: BizTodo, task: BizTask, kind: str, assignee_name: str = "") -> None:
        items.append({
            "todo_id": t.todo_id, "task_id": t.task_id, "title": t.title,
            "status": t.status, "kind": kind, "due": _fmt_due(t),
            "assignee_name": assignee_name,
        })

    # 处理人侧：我名下进行中（不含 submitted——那是等派发人，不是我今天的活）
    todos = db.scalars(
        select(BizTodo).where(
            BizTodo.assignee_id == user_id,
            BizTodo.status.in_(("pending", "returned", "na_pending")),
        )
    ).all()
    for t in todos:
        task = db.get(BizTask, t.task_id)
        if task is None or task.status != "open":
            continue
        if t.status == "returned":
            add(t, task, "returned")
        elif derive_overdue(t, task, today) and not period_locked(task, today):
            add(t, task, "overdue")
        elif t.due and t.due.astimezone().date() == today.date():
            add(t, task, "due_today")

    # 派发人侧：待审 / 不涉及待确认 / 下发超 2 天未反馈（催办候选）
    open_tasks = db.scalars(
        select(BizTask).where(BizTask.dispatcher_id == user_id, BizTask.status == "open")
    ).all()
    task_ids = [k.task_id for k in open_tasks]
    if task_ids:
        rows = db.scalars(
            select(BizTodo).where(
                BizTodo.task_id.in_(task_ids),
                BizTodo.status.in_(("submitted", "na_pending", "pending", "returned")),
            )
        ).all()
        task_of = {k.task_id: k for k in open_tasks}
        for t in rows:
            name = _display_name(db, t.assignee_id)
            if t.status == "submitted":
                add(t, task_of[t.task_id], "to_review", name)
            elif t.status == "na_pending":
                add(t, task_of[t.task_id], "to_na_confirm", name)
            elif t.status in ("pending", "returned"):
                days = (today - t.created_at).days
                if days >= 2 and (t.remind_count or 0) == 0:
                    add(t, task_of[t.task_id], "silent", name)

    rank = {"overdue": 0, "due_today": 1, "returned": 2, "to_review": 3, "to_na_confirm": 4, "silent": 5}
    items.sort(key=lambda i: (rank.get(i["kind"], 9), i["due"] or "9999"))
    counts: dict[str, int] = {}
    for i in items:
        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    return {"date": today.date().isoformat(), "counts": counts, "items": items[:20]}


def _scan_and_publish() -> None:
    """晨间扫描：有活动待办的用户逐一聚合，在线者推 brief/ready，离线者首屏拉取兜底。"""
    with SessionLocal() as db:
        user_ids = set(db.scalars(
            select(BizTodo.assignee_id).where(BizTodo.status.in_(("pending", "returned", "na_pending", "submitted")))
        ).all())
        user_ids.update(db.scalars(
            select(BizTask.dispatcher_id).where(BizTask.status == "open")
        ).all())
        briefs = {uid: daily_brief(db, uid) for uid in user_ids if uid}
    online = online_user_ids()
    pushed = 0
    for uid in online:
        b = briefs.get(uid)
        if b and b["items"]:
            publish([uid], "brief/ready", b)
            pushed += 1
    logger.info("晨间扫描完成 用户=%d 在线推送=%d", len(briefs), pushed)


def start_daily_scan() -> None:
    """daemon：睡到下一个 SCAN_HOUR 整点 → 扫描发布 → 循环。"""

    def _loop() -> None:
        while True:
            now = datetime.now().astimezone()
            target = now.replace(hour=SCAN_HOUR, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            time.sleep(max(1.0, (target - now).total_seconds()))
            try:
                _scan_and_publish()
            except Exception:
                logger.exception("晨间扫描失败（次日重试）")

    threading.Thread(target=_loop, name="todo-daily-scan", daemon=True).start()
