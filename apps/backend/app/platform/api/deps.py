from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import errors
from ...persistence.models import SysAuthToken, SysUser
from ...persistence.session import get_db
from ..xuanpu_perm import PERM_FINANCE_MANAGE, user_permissions

_bearer = HTTPBearer(auto_error=False)


def user_view(u: SysUser) -> dict:
    return {
        "user_id": u.user_id,
        "username": u.username,
        "display_name": u.display_name,
        "role": u.role,
        "company": u.company,
        "permissions": user_permissions(u.xuanpu_user_id),
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
    """财务管理工作台门禁（XuanPu 统一权限驱动；本地账号全权限）"""
    return require_perm(PERM_FINANCE_MANAGE)(user)


def require_perm(code: str):
    """按 XuanPu 统一权限点校验（60s 缓存；未绑定统一身份的本地账号放行）"""

    def _dep(user: SysUser = Depends(current_user)) -> SysUser:
        if code not in user_permissions(user.xuanpu_user_id):
            raise errors.forbidden("未开通该功能权限，请联系管理员在统一权限管理中授权")
        return user

    return _dep
