"""消息中心服务：任务流转通知的产生、查询与已读维护。

scms 侧 f540c0a 提交遗漏本文件；按调用方契约重建：
- todo.py._create_notification(...) → create(...) 落库 + todo_bus 推 notification/created
- 契约 packages/contracts/schemas/task.json 的 NotificationItem / NotificationList
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..persistence.models import UserNotification
from .common import dispatcher_name
from ..platform.events import todo_bus

logger = logging.getLogger(__name__)

# 首屏拉取条数上限（前端分组展示未读/已读，历史消息分页按需扩展）
DEFAULT_LIMIT = 50


def create(
    db: Session, *, recipient_id: str, actor_id: str, category: str,
    title: str, body: str, todo_id: str, task_id: str,
) -> UserNotification:
    row = UserNotification(
        recipient_id=recipient_id, actor_id=actor_id, category=category,
        title=title, body=body, todo_id=todo_id, task_id=task_id,
    )
    db.add(row)
    db.flush()
    item = view(db, row)
    try:
        todo_bus.publish([recipient_id], "notification/created", item)
    except Exception:
        logger.warning("notification/created 推送失败 recipient=%s", recipient_id, exc_info=True)
    return row


def view(db: Session, n: UserNotification) -> dict:
    """NotificationItem 契约视图。"""
    return {
        "notification_id": n.notification_id,
        "category": n.category,
        "title": n.title,
        "body": n.body,
        "todo_id": n.todo_id,
        "task_id": n.task_id,
        "actor_id": n.actor_id,
        "actor_name": dispatcher_name(db, n.actor_id),
        "is_read": bool(n.is_read),
        "created_at": (n.created_at or datetime.now(timezone.utc)).isoformat(),
    }


def list_for_user(
    db: Session, user_id: str, *, unread_only: bool = False, limit: int = DEFAULT_LIMIT,
) -> dict:
    """NotificationList：最近通知 + 未读数。"""
    q = select(UserNotification).where(UserNotification.recipient_id == user_id)
    if unread_only:
        q = q.where(UserNotification.is_read.is_(False))
    rows = db.scalars(
        q.order_by(UserNotification.created_at.desc()).limit(limit)
    ).all()
    unread = db.scalar(
        select(func.count()).select_from(UserNotification).where(
            UserNotification.recipient_id == user_id,
            UserNotification.is_read.is_(False),
        )
    ) or 0
    return {"items": [view(db, r) for r in rows], "unread_count": int(unread)}


def mark_read(db: Session, user_id: str, notification_id: str) -> dict:
    n = db.get(UserNotification, notification_id)
    if n is None or n.recipient_id != user_id:
        from ..core import errors
        raise errors.not_found("消息不存在")
    if not n.is_read:
        n.is_read = True
        n.read_at = datetime.now(timezone.utc)
        db.flush()
    return view(db, n)


def mark_all_read(db: Session, user_id: str) -> int:
    rows = db.scalars(
        select(UserNotification).where(
            UserNotification.recipient_id == user_id,
            UserNotification.is_read.is_(False),
        )
    ).all()
    now = datetime.now(timezone.utc)
    for n in rows:
        n.is_read = True
        n.read_at = now
    db.flush()
    return len(rows)
