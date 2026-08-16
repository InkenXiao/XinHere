from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, Request
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


class LoginIn(BaseModel):
    username: str
    password: str


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
    if user is None or not bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
        raise errors.unauthorized("用户名或口令错误")
    # 旧 token 逻辑失效（红线1：UPDATE is_delete，不 DELETE）
    for old in db.scalars(select(SysAuthToken).where(SysAuthToken.user_id == user.user_id)).all():
        old.is_delete = True
    token = secrets.token_urlsafe(32)
    db.add(
        SysAuthToken(
            token=token,
            user_id=user.user_id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.token_ttl_hours),
        )
    )
    db.flush()
    return {"token": token, "user": user_view(user)}


@router.post("/logout")
def logout(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    for old in db.scalars(select(SysAuthToken).where(SysAuthToken.user_id == user.user_id)).all():
        old.is_delete = True
    return {"ok": True}


@router.get("/me")
def me(user: SysUser = Depends(current_user)):
    return user_view(user)
