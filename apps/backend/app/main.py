from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .core.config import settings
from .core.context import AuditCtx, reset_ctx, set_ctx
from .core.errors import AppError
from .persistence.models import SysAuthToken, SysUser
from .persistence.session import SessionLocal
from .platform.agent.executor import executor
from .platform.api import (
    auth,
    cash,
    dashboard,
    kb,
    kpi,
    plugins,
    reports,
    risk_fills,
    sessions,
    todos,
)
from .platform.plugins.loader import discover

# uvicorn 默认 log config 不含 root handler，业务 logger 需 basicConfig 才能输出
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="XinHere Backend", docs_url=None, redoc_url=None, openapi_url=None)

logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def audit_ctx(request: Request, call_next):
    """审计上下文装配：页面请求 channel=page；Bearer 有效则归因到用户。

    必须在路由处理器之前于事件循环上下文 set_ctx——sync 路由/依赖经
    threadpool 运行时复制当前 context，中间件所设值随之传播；请求结束 reset。
    无 token/无效 token 不阻断（401 由 deps 判定），仅留 anonymous 归因。
    """
    path = request.url.path
    ctx = AuditCtx(
        user_id="anonymous",
        channel="page",
        actor=f"page:{path}",
        request_id=getattr(request.state, "request_id", ""),
        client_ip=request.client.host if request.client else None,
        entry_point=f"{request.method} {path}",
    )
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        cred = auth[7:].strip()
        if cred:
            try:
                with SessionLocal() as db:
                    row = db.scalars(
                        select(SysAuthToken).where(SysAuthToken.token == cred)
                    ).first()
                    if row is not None and row.expires_at >= datetime.now(timezone.utc):
                        user = db.get(SysUser, row.user_id)
                        if user is not None:
                            ctx.user_id = user.user_id
                            ctx.actor = user.display_name or user.username
            except Exception:
                pass  # 认证解析异常不阻断请求，交由 deps 做 401
    token = set_ctx(ctx)
    try:
        return await call_next(request)
    finally:
        reset_ctx(token)


@app.middleware("http")
async def host_fence(request: Request, call_next):
    """Host 头围栏：白名单 loopback + 配置项。"""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    allowed = {"localhost", "127.0.0.1", "::1", *settings.allowed_host_list}
    if host and host not in allowed:
        return JSONResponse(
            status_code=403,
            content={"code": "FORBIDDEN", "message": "Host 不在白名单"},
        )
    return await call_next(request)


@app.middleware("http")
async def request_id(request: Request, call_next):
    rid = uuid.uuid4().hex[:16]
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    body = {"code": exc.code, "message": exc.message}
    if exc.details:
        body["details"] = exc.details
    return JSONResponse(status_code=exc.status, content=body)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"code": "VALIDATION_ERROR", "message": "请求参数不合法",
                 "details": {"errors": exc.errors()[:5]}},
    )


@app.exception_handler(Exception)
async def internal_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"code": "INTERNAL", "message": str(exc)[:200]})


@app.get("/healthz")
def healthz():
    return {"ok": True}


API = "/api/v1"
for r in (
    auth.router, sessions.router, todos.router, dashboard.router, risk_fills.router,
    cash.router, kpi.router, reports.router, kb.router, plugins.router,
):
    app.include_router(r, prefix=API)


@app.on_event("startup")
def startup():
    discover()  # 插件装配 fail-loud
    logger.info("启动：插件装配完成")

    def _init():
        executor.setup()  # checkpoint 表
        logger.info("启动：checkpointer 就绪")
        n = executor.recover_crashed()  # 崩溃恢复：补 turn/end{crashed} 与 tool/result{unknown}
        logger.info("启动：崩溃恢复完成 recovered=%d", n)

    threading.Thread(target=_init, name="agent-init", daemon=True).start()
