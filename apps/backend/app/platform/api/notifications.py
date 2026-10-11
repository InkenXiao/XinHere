"""消息中心 API：列表（未读数） / 单条已读 / 全部已读。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import notification as notification_svc
from .deps import current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    user: SysUser = Depends(current_user), db: Session = Depends(get_db),
):
    return notification_svc.list_for_user(db, user.user_id, unread_only=unread_only, limit=limit)


class ReadIn(BaseModel):
    notification_id: str | None = None  # 空 = 全部已读


@router.post("/read")
def read(body: ReadIn, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    if body.notification_id:
        item = notification_svc.mark_read(db, user.user_id, body.notification_id)
        return {"item": item}
    n = notification_svc.mark_all_read(db, user.user_id)
    return {"marked": n}
