from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...persistence.models import SysUser
from ...services import repo_alerts
from .deps import current_user

router = APIRouter(prefix="/repo-alerts", tags=["repo-alerts"])


@router.get("/status")
def repo_alert_status(user: SysUser = Depends(current_user)):
    """逆回购流接入状态（连接态/最近心跳/累计数），日志页头部卡片数据源。"""
    return repo_alerts.status()


@router.get("/log")
def repo_alert_log(limit: int = Query(200, ge=1, le=1000),
                   user: SysUser = Depends(current_user)):
    """接入流水日志（连接/心跳/提醒/断线，环形 1000 条，新事件在前端轮询刷新）。"""
    return {"items": repo_alerts.log_items(limit)}
