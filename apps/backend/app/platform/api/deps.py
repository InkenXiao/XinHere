from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import errors
from ...persistence.models import SysAuthToken, SysUser
from ...persistence.session import get_db

_bearer = HTTPBearer(auto_error=False)


def user_view(u: SysUser) -> dict:
    return {
        "user_id": u.user_id,
        "username": u.username,
        "display_name": u.display_name,
        "role": u.role,
        "company": u.company,
    }


def current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> SysUser:
    # 审计上下文由 main.py 的 audit_ctx 中间件装配（threadpool 副本内 set_ctx 传不到路由处理器）
    if cred is None:
        raise errors.unauthorized("缺少 Bearer token")
    row = db.scalars(select(SysAuthToken).where(SysAuthToken.token == cred.credentials)).first()
    if row is None or row.expires_at < datetime.now(timezone.utc):
        raise errors.unauthorized("token 无效或已过期")
    user = db.get(SysUser, row.user_id)
    if user is None:
        raise errors.unauthorized("用户不存在")
    return user


def require_hq(user: SysUser = Depends(current_user)) -> SysUser:
    if user.role != "hq_finance":
        raise errors.forbidden("仅本部财务可执行")
    return user
