from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class AuditCtx:
    """当前操作上下文：操作日志五要素来源。"""

    user_id: str = "system"
    channel: str = "system"  # page/agent/model/skill/mcp/tool/api/system
    actor: str = ""
    session_id: str | None = None
    request_id: str = ""
    client_ip: str | None = None
    entry_point: str = ""
    detail_context: dict = field(default_factory=dict)  # 工具调用上下文凭证


_current: ContextVar[AuditCtx] = ContextVar("audit_ctx", default=AuditCtx())


def get_ctx() -> AuditCtx:
    return _current.get()


def set_ctx(ctx: AuditCtx):
    return _current.set(ctx)


def reset_ctx(token) -> None:
    _current.reset(token)
