from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import bcrypt
import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import errors
from ...core.config import settings
from ...core.context import AuditCtx, set_ctx
from ...persistence.models import SysAuthToken, SysUser
from ...persistence.session import get_db
from .deps import current_user, user_view

router = APIRouter(prefix="/auth", tags=["auth"])

# SSO state 有效期 (秒): 一次授权往返足够, 防重放
_SSO_STATE_TTL = 600
# 内存 state 表 (backend 单进程 uvicorn; {state: 过期时间戳})
_sso_states: dict[str, float] = {}


class LoginIn(BaseModel):
    username: str
    password: str


def _issue_token(
    db: Session,
    user: SysUser,
    xuanpu_token: str | None = None,
    xuanpu_token_exp: datetime | None = None,
) -> str:
    """签发登录 token；旧 token 全部失效（红线1：UPDATE is_delete，不 DELETE）。"""
    for old in db.scalars(select(SysAuthToken).where(SysAuthToken.user_id == user.user_id)).all():
        old.is_delete = True
    token = secrets.token_urlsafe(32)
    db.add(
        SysAuthToken(
            token=token,
            user_id=user.user_id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.token_ttl_hours),
            xuanpu_token=xuanpu_token,
            xuanpu_token_exp=xuanpu_token_exp,
        )
    )
    db.flush()
    return token


def _cleanup_states(now: float) -> None:
    for s, exp in list(_sso_states.items()):
        if exp < now:
            _sso_states.pop(s, None)


def _bind_xuanpu_user(
    db: Session, xuanpu_user_id: str, user_code: str, user_name: str, is_admin: bool
) -> SysUser:
    """JIT 建号 / 幂等绑定：优先按 XuanPu 绑定查，其次按登录账号名对齐已有本地账号。"""
    user = db.scalars(select(SysUser).where(SysUser.xuanpu_user_id == xuanpu_user_id)).first()
    if user is None and user_code:
        user = db.scalars(select(SysUser).where(SysUser.username == user_code)).first()
    if user is None:
        user = SysUser(
            username=user_code or xuanpu_user_id,
            password_hash="",  # SSO 账号无本地密码
            display_name=user_name,
            role="hq_finance" if is_admin else "investee_finance",
            company="",
            auth_source="sso",
            xuanpu_user_id=xuanpu_user_id,
        )
        db.add(user)
        db.flush()
    else:
        # 已有账号：同步最新姓名与绑定，不动本地角色（避免覆盖管理员配置）
        user.display_name = user_name
        user.xuanpu_user_id = xuanpu_user_id
        user.auth_source = "sso"
    return user


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    set_ctx(
        AuditCtx(
            user_id="anonymous",
            channel="page",
            actor="page:login",
            request_id=getattr(request.state, "request_id", ""),
            client_ip=request.client.host if request.client else None,
            entry_point="POST /api/v1/auth/login",
        )
    )
    user = db.scalars(select(SysUser).where(SysUser.username == body.username)).first()
    # SSO 建号无本地密码 (password_hash 为空), 不允许走本地口令登录
    if (
        user is None
        or not user.password_hash
        or not bcrypt.checkpw(body.password.encode(), user.password_hash.encode())
    ):
        raise errors.unauthorized("用户名或口令错误")
    token = _issue_token(db, user)
    # 登录关键路径：立即提交，确保响应发出前 token 已落库，
    # 避免客户端拿到 token 立即请求时偶发 401（新 token 短暂不可见）。
    db.commit()
    return {"token": token, "user": user_view(user)}


@router.post("/logout")
def logout(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    for old in db.scalars(select(SysAuthToken).where(SysAuthToken.user_id == user.user_id)).all():
        old.is_delete = True
    return {"ok": True}


@router.get("/me")
def me(user: SysUser = Depends(current_user)):
    return user_view(user)


# ---------------- 统一身份登录 (XuanPu SSO) ----------------


@router.get("/sso/login")
def sso_login() -> RedirectResponse:
    """跳转统一身份登录：302 到 sso-server 授权页，state 内存登记防 CSRF。"""
    now = time.time()
    _cleanup_states(now)
    state = secrets.token_urlsafe(16)
    _sso_states[state] = now + _SSO_STATE_TTL
    params = urlencode(
        {
            "response_type": "code",
            "client_id": settings.sso_client_id,
            "redirect_uri": settings.sso_callback_url,
            "state": state,
        }
    )
    return RedirectResponse(f"{settings.sso_auth_url}?{params}", status_code=302)


@router.get("/sso/callback")
def sso_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: Session = Depends(get_db),
) -> RedirectResponse:
    set_ctx(
        AuditCtx(
            user_id="anonymous",
            channel="page",
            actor="page:sso_callback",
            request_id=getattr(request.state, "request_id", ""),
            client_ip=request.client.host if request.client else None,
            entry_point="GET /api/v1/auth/sso/callback",
        )
    )
    now = time.time()
    _cleanup_states(now)
    if error or not code:
        raise errors.validation("统一身份登录未完成授权")
    # state 一次性校验（弹出即失效）
    exp = _sso_states.pop(state, None)
    if exp is None or exp < now:
        raise errors.validation("登录状态已失效，请重新登录")

    # 授权码换 token（sso-server 容器内网直达）
    try:
        resp = httpx.post(
            settings.sso_token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.sso_client_id,
                "client_secret": settings.sso_client_secret,
                "redirect_uri": settings.sso_callback_url,
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise errors.upstream(f"统一身份服务不可达：{exc}") from exc
    if resp.status_code != 200:
        raise errors.upstream("统一身份登录凭证交换失败")
    payload = resp.json()
    xuanpu_user = payload.get("user") or {}
    xuanpu_user_id = str(xuanpu_user.get("user_id") or "")
    if not xuanpu_user_id:
        raise errors.upstream("统一身份服务返回用户信息不完整")
    user_code = str(xuanpu_user.get("user_code") or "")
    user_name = str(xuanpu_user.get("user_name") or user_code)
    is_admin = bool(xuanpu_user.get("is_admin"))

    # JIT 建号 / 幂等绑定
    user = _bind_xuanpu_user(db, xuanpu_user_id, user_code, user_name, is_admin)

    xuanpu_token = payload.get("access_token")
    xuanpu_token_exp = None
    expires_in = payload.get("expires_in")
    if xuanpu_token and isinstance(expires_in, (int, float)):
        xuanpu_token_exp = datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))
    token = _issue_token(db, user, xuanpu_token=xuanpu_token, xuanpu_token_exp=xuanpu_token_exp)
    # 同登录路径：立即提交，保证 302 落地后 token 立即可用。
    db.commit()
    return RedirectResponse(
        f"{settings.frontend_url}/?sso_token={quote(token, safe='')}", status_code=302
    )


@router.get("/sso/ticket")
def sso_ticket(
    request: Request,
    ticket: str = "",
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """XuanPu 门户免登入口：消费 sso-server 一次性 ticket → 绑定/建号 → 签发本地 token
    → 302 前端 ?sso_token= 自动登录。无 ticket 时 302 前端走常规登录。"""
    set_ctx(
        AuditCtx(
            user_id="anonymous",
            channel="page",
            actor="page:sso_ticket",
            request_id=getattr(request.state, "request_id", ""),
            client_ip=request.client.host if request.client else None,
            entry_point="GET /api/v1/auth/sso/ticket",
        )
    )
    if not ticket:
        return RedirectResponse(settings.frontend_url, status_code=302)
    try:
        resp = httpx.post(settings.sso_verify_url, json={"ticket": ticket}, timeout=15)
    except httpx.HTTPError as exc:
        raise errors.upstream(f"统一身份服务不可达：{exc}") from exc
    if resp.status_code != 200:
        raise errors.upstream("统一身份登录凭证校验失败")
    payload = resp.json()
    if not payload.get("ok"):
        return RedirectResponse(settings.frontend_url, status_code=302)
    xuanpu_user = payload.get("user") or {}
    xuanpu_user_id = str(xuanpu_user.get("user_id") or "")
    if not xuanpu_user_id:
        raise errors.upstream("统一身份服务返回用户信息不完整")
    user_code = str(xuanpu_user.get("user_code") or "")
    user_name = str(xuanpu_user.get("user_name") or user_code)
    is_admin = bool(xuanpu_user.get("is_admin"))

    user = _bind_xuanpu_user(db, xuanpu_user_id, user_code, user_name, is_admin)

    xuanpu_token = payload.get("access_token")
    xuanpu_token_exp = None
    expires_in = payload.get("expires_in")
    if xuanpu_token and isinstance(expires_in, (int, float)):
        xuanpu_token_exp = datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))
    token = _issue_token(db, user, xuanpu_token=xuanpu_token, xuanpu_token_exp=xuanpu_token_exp)
    # 同登录路径：立即提交，保证 302 落地后 token 立即可用。
    db.commit()
    return RedirectResponse(
        f"{settings.frontend_url}/?sso_token={quote(token, safe='')}", status_code=302
    )
