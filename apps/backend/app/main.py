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

from .core import config_check
from .platform.daemons import daemon_states
from .core.config import settings
from .core.context import AuditCtx, reset_ctx, set_ctx
from .core.errors import AppError
from .persistence.models import SysAuthToken, SysUser
from .persistence.session import SessionLocal
from .platform.agent.executor import executor
from .platform.api import (
    artifacts,
    attachments,
    auth,
    briefing,
    config as config_api,
    cowork,
    org,
    repo_alerts,
    scms,
    voiceprint,
    html_reports,
    ppt_reports,
    cash,
    cockpit,
    dashboard,
    hero,
    kb,
    kpi,
    plugins,
    reports,
    risk_fills,
    sessions,
    skills,
    tasks,
    todos,
    xuanpu,
    models as models_router,
    media as media,
    minutes as minutes,
    metrics as metrics,
    model_prefs as model_prefs,
    notifications,
)
from .modules.dispatch import api as dispatch_api
from .modules.dynamic_template import api as dynamic_template_api
from .modules.meeting import api as meeting_api
from .platform.plugins.loader import discover, plugin_routers

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
    cc = config_check.last_result()
    return {
        "ok": True,
        "config_check": {"ok": cc["ok"], "problems": cc["problems"], "warnings": cc["warnings"]},
        "daemons": daemon_states.snapshot(),
    }


API = "/api/v1"
for r in (
    auth.router, sessions.router, todos.router, dashboard.router, risk_fills.router,
    cash.router, kpi.router, reports.router, kb.router, plugins.router,
    xuanpu.router, cockpit.router, tasks.router, hero.router,
    models_router.router, media.router, minutes.router, skills.router,
    metrics.router, model_prefs.router, artifacts.router, notifications.router,
    voiceprint.router, html_reports.router, ppt_reports.router,
    briefing.router, cowork.router, config_api.router, scms.router,
    org.router, repo_alerts.router, attachments.router,
    dispatch_api.router, dynamic_template_api.router, meeting_api.router,
):
    app.include_router(r, prefix=API)

# 插件自带 REST（如 oa-workflow /api/v1/oa/*；移除插件目录即消失）
for r in plugin_routers():
    app.include_router(r, prefix=API)


@app.on_event("startup")
def startup():
    config_check.enforce()  # 配置健康检查：域开关→必填项矩阵，缺依赖拒绝启动（Fail-Fast）
    discover()  # 插件装配 fail-loud
    from .services import task_templates

    task_templates.discover_templates()  # 任务模板 fail-loud（大流程固定/小流程可插拔）
    logger.info("启动：插件装配完成")

    try:
        with SessionLocal() as db:
            n = cockpit.seed_entries(db)  # Xin台入口幂等 seed（失败不阻断启动）
            n += cockpit.seed_cards(db)  # Xin台分组卡片幂等 seed
            n += cockpit.seed_dashboard(db)  # 驾驶舱（dashboard surface）幂等 seed
            db.commit()
        if n:
            logger.info("启动：Xin台/驾驶舱配置 seed 新增 %d 条", n)
    except Exception:
        logger.exception("启动：Xin台配置 seed 失败")

    _start_daemons()

    def _init():
        executor.setup()  # checkpoint 表
        logger.info("启动：checkpointer 就绪")
        n = executor.recover_crashed()  # 崩溃恢复：补 turn/end{crashed} 与 tool/result{unknown}
        logger.info("启动：崩溃恢复完成 recovered=%d", n)

    threading.Thread(target=_init, name="agent-init", daemon=True).start()


# ---------------- 后台守护（§4.9：DB 租约互斥 + 开关联动；异常不拖垮主进程） ----------------

_daemon_threads: list = []


def _guarded_daemon(name: str, starter) -> None:
    """租约互斥启动：抢不到租约（其他副本持有）→ standby 不启动；抢到 → 启动并周期续约。"""
    from .platform.daemons import acquire_or_renew, daemon_states, release

    if not acquire_or_renew(name, ttl_seconds=90):
        daemon_states.set(name, "standby", "其他副本持有租约")
        logger.info("[daemon:%s] 其他副本持有租约，本副本不启动", name)
        return

    import threading
    import time as _time

    daemon_states.set(name, "running")
    try:
        starter()
    except Exception:
        daemon_states.set(name, "circuit-broken", "启动失败")
        logger.exception("[daemon:%s] 启动失败，熔断", name)
        release(name)
        return

    def _renew():
        while True:
            _time.sleep(30)
            if not acquire_or_renew(name, ttl_seconds=90):
                daemon_states.set(name, "standby", "租约丢失/被接管")
                logger.warning("[daemon:%s] 租约丢失，停止续约（请人工检查该守护停机）", name)
                return

    th = threading.Thread(target=_renew, name=f"daemon-{name}-renew", daemon=True)
    th.start()
    _daemon_threads.append(th)


def _start_daemons() -> None:
    import asyncio

    loop = asyncio.get_event_loop()

    # 触达晨间扫描（brief/ready）
    def _start_touch():
        from .services import touch

        touch.start_daily_scan()

    _guarded_daemon("touch_daily_scan", _start_touch)

    # 逆回购公共流 WS 守护（REPO_ALERT_WS_URL 未配置=不启动）
    if settings.repo_alert_ws_url:
        def _start_repo():
            from .services import repo_alerts as repo_alerts_svc

            repo_alerts_svc.start()

        _guarded_daemon("repo_alerts_ws", _start_repo)
    else:
        from .platform.daemons import daemon_states

        daemon_states.set("repo_alerts_ws", "disabled", "REPO_ALERT_WS_URL 未配置")

    # 会议超时看门狗
    def _start_watchdog():
        from .modules.meeting import service as meeting_service

        meeting_service.start_watchdog()

    _guarded_daemon("meeting_watchdog", _start_watchdog)


# MCP 服务端（默认关；开启后 /mcp 供外部 agent 客户端读写任务平台）
if settings.mcp_server_enabled:
    from .platform.mcp_server import mcp_app

    app.mount("/mcp", mcp_app)


@app.on_event("shutdown")
def shutdown():
    try:
        from .platform.agent import tracing

        tracing.shutdown()  # Langfuse 观测：退出前 flush 尾部 trace（未启用时静默）
    except Exception:
        pass
    from .platform.daemons import daemon_states

    for name in list(_daemon_threads and daemon_states.snapshot().keys()):
        from .platform.daemons import release

        release(name)
