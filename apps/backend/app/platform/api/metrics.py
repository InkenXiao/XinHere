from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import metrics as metrics_svc
from .deps import current_user

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/overview")
def overview(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """当前用户的任务看板 + 数据看板统计"""
    return metrics_svc.user_overview(db, user.user_id)
