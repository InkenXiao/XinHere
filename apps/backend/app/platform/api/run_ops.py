"""P9 §3.2 会话运行对账的人工出口：异常查询 + 处置动作。

只读查询与三动作（ack 确认误报 / force_error 强制置错 / reclaim 换代次重置）
均走服务层 app.platform.daemons.run_reconcile；动作幂等（同 anomaly 同动作
只生效一次，重复提交返回 duplicate=true），全部落操作日志审计。
权限沿用现有管理门禁 require_hq（统一权限驱动，本地账号全权限）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...core import errors
from ...persistence.models import SysUser
from ...persistence.session import get_db
from ..daemons import run_reconcile
from .deps import require_hq

router = APIRouter(prefix="/run-anomalies", tags=["run-ops"])


@router.get("")
def list_(
    session_id: str | None = None,
    limit: int = Query(50, le=200),
    user: SysUser = Depends(require_hq),  # noqa: ARG001 - 门禁
    db: Session = Depends(get_db),
):
    """会话运行异常记录查询（可按会话过滤；返回处置状态与最近一次动作）。"""
    return {"items": run_reconcile.list_anomalies(db, session_id, limit)}


class ActionIn(BaseModel):
    anomaly_id: int
    action: str  # ack / force_error / reclaim


@router.post("/{session_id}/actions")
def act(
    session_id: str,
    body: ActionIn,
    user: SysUser = Depends(require_hq),
    db: Session = Depends(get_db),
):
    """处置会话运行异常：确认误报 / 强制结束 / 重置会话。同一异常同一动作只生效一次。"""
    if body.action not in run_reconcile.ACTIONS:
        raise errors.validation("不支持的处理动作")
    try:
        return run_reconcile.apply_action(
            db, session_id, body.action, body.anomaly_id,
            actor=user.user_id, entry_point="POST /run-anomalies/actions",
        )
    except LookupError:
        raise errors.not_found("异常记录不存在") from None
