from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from langgraph.errors import GraphInterrupt

from ...core.config import settings
from ...core.context import AuditCtx, reset_ctx, set_ctx
from ...persistence.session import SessionLocal

logger = logging.getLogger(__name__)


@dataclass
class ToolCtx:
    """工具上下文：数据权限与审计溯源绑定（07 §6）。"""

    user_id: str
    session_id: str
    request_id: str
    turn: int = 0
    emit: Callable[[str, dict], tuple[int, datetime]] | None = None  # store.append
    extra: dict = field(default_factory=dict)

    def emit_event(self, type_: str, payload: dict) -> tuple[int, datetime] | None:
        if self.emit is None:
            return None
        return self.emit(type_, payload)


@contextmanager
def tool_scope(ctx: ToolCtx, name: str, call_id: str, arguments: dict):
    """工具执行域：注入审计上下文（channel=tool，actor=模型+工具名），提供落库 session。

    detail.context 携带 {call_id, tool_call_arguments, operator_user_id}（AI 溯源）。
    """
    audit = AuditCtx(
        user_id=ctx.user_id,
        channel="tool",
        actor=f"{settings.main_model}+{name}",
        session_id=ctx.session_id or None,
        request_id=ctx.request_id,
        entry_point=name,
        detail_context={
            "context": {
                "call_id": call_id or "",
                "tool_call_arguments": arguments,
                "operator_user_id": ctx.user_id,
            }
        },
    )
    token = set_ctx(audit)
    try:
        with SessionLocal() as db:
            try:
                yield db
                db.commit()
                logger.info("tool_scope 提交 sid=%s tool=%s call_id=%s user=%s",
                            ctx.session_id, name, call_id, ctx.user_id)
            except GraphInterrupt:
                # interrupt 挂起是正常控制流：未提交写随会话关闭丢弃，不记回滚告警
                logger.info("tool_scope 挂起（interrupt） sid=%s tool=%s call_id=%s",
                            ctx.session_id, name, call_id)
                raise
            except Exception as exc:
                logger.warning("tool_scope 回滚 sid=%s tool=%s call_id=%s err=%r",
                               ctx.session_id, name, call_id, exc)
                raise
    finally:
        reset_ctx(token)


def new_component_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex[:12]}"
