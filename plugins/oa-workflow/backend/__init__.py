"""oa-workflow 插件 backend 装配：暴露 make_tools / router，并向平台注册组件卡处理器。

注册在 discover() 导入本包时执行（幂等：同函数重复注册不报错，不同函数重名 fail-loud）。
"""
from __future__ import annotations

from . import perm, service  # noqa: F401
from .router import router  # noqa: F401
from .tools import make_tools  # noqa: F401


def _register_cards() -> None:
    from app.platform.agent.component_handlers import register

    from . import handlers

    register("oa-dispatch-confirm", handlers.handle_oa_dispatch_confirm)
    register("oa-request-card", handlers.handle_oa_request_card)
    register("oa-audit-card", handlers.handle_oa_audit_card)


_register_cards()
