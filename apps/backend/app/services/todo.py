from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import errors
from ..persistence.models import BizTask, BizTodo, SysUser
from .common import todo_view

ACTIVE_STATUSES = ("pending", "na_pending", "feedback_submitted", "submitted")


def create_task(
    db: Session, *, scene: str, title: str, dispatcher_id: str, payload: dict | None = None,
    period: str | None = None,
) -> BizTask:
    task = BizTask(scene=scene, title=title, dispatcher_id=dispatcher_id, payload=payload or {}, period=period)
    db.add(task)
    db.flush()
    return task


def create_todo(
    db: Session, *, task: BizTask, assignee_id: str, kind: str = "action", title: str | None = None,
    sub: str = "", lamp: str | None = None, ref: dict | None = None, due=None,
) -> BizTodo:
    todo = BizTodo(
        task_id=task.task_id,
        assignee_id=assignee_id,
        kind=kind,
        scene=task.scene,
        title=title or task.title,
        sub=sub,
        lamp=lamp,
        ref=ref or {},
        due=due,
    )
    db.add(todo)
    db.flush()
    return todo


def list_todos(db: Session, user: SysUser, box: str) -> list[dict]:
    if box == "assignee":
        rows = db.scalars(
            select(BizTodo)
            .where(BizTodo.assignee_id == user.user_id, BizTodo.status != "na_closed")
            .order_by(BizTodo.created_at.desc())
        ).all()
    else:
        task_ids = select(BizTask.task_id).where(BizTask.dispatcher_id == user.user_id)
        rows = db.scalars(
            select(BizTodo)
            .where(BizTodo.task_id.in_(task_ids), BizTodo.status != "na_closed")
            .order_by(BizTodo.created_at.desc())
        ).all()
    return [todo_view(db, t) for t in rows]


def _get(db: Session, todo_id: str) -> BizTodo:
    t = db.get(BizTodo, todo_id)
    if t is None:
        raise errors.not_found("待办不存在")
    return t


def _dispatcher_review_todo(db: Session, src: BizTodo, kind: str, sub_suffix: str) -> None:
    task = db.get(BizTask, src.task_id)
    if task is None:
        return
    create_todo(
        db,
        task=task,
        assignee_id=task.dispatcher_id,
        kind=kind,
        title=f"{'不涉及确认' if kind == 'na_confirm' else '反馈复核'}：{src.title}",
        sub=sub_suffix,
        ref={**src.ref, "src_todo_id": src.todo_id},
    )


def feedback(db: Session, todo_id: str, text: str) -> dict:
    t = _get(db, todo_id)
    if t.status not in ("pending",):
        raise errors.validation(f"当前状态 {t.status} 不允许反馈")
    t.feedback_text = text
    t.status = "feedback_submitted"
    _dispatcher_review_todo(db, t, "feedback_review", f"反馈：{text[:50]}")
    db.flush()
    return todo_view(db, t)


def na(db: Session, todo_id: str, reason: str) -> dict:
    t = _get(db, todo_id)
    if t.status not in ("pending",):
        raise errors.validation(f"当前状态 {t.status} 不允许标记不涉及")
    t.na_reason = reason
    t.status = "na_pending"
    _dispatcher_review_todo(db, t, "na_confirm", f"不涉及原因：{reason[:50]}")
    db.flush()
    return todo_view(db, t)


def na_confirm(db: Session, todo_id: str) -> dict:
    t = _get(db, todo_id)
    if t.status != "na_pending":
        raise errors.validation("仅 na_pending 状态可确认")
    t.status = "na_closed"
    db.flush()
    return todo_view(db, t)


def na_reject(db: Session, todo_id: str, comment: str | None) -> dict:
    t = _get(db, todo_id)
    if t.status != "na_pending":
        raise errors.validation("仅 na_pending 状态可驳回")
    t.status = "pending"
    t.na_comment = comment
    db.flush()
    return todo_view(db, t)


def complete(db: Session, todo_id: str) -> dict:
    t = _get(db, todo_id)
    if t.status in ("completed", "na_closed"):
        raise errors.validation("待办已终结")
    t.status = "completed"
    db.flush()
    return todo_view(db, t)
