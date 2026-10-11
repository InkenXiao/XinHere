from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session



from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import identity as identity_svc
from .deps import current_user

router = APIRouter(prefix="/org", tags=["org"])


@router.get("/me")
def me(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """当前登录人身份（角色/公司/角色码）——前端按角色渲染按钮的依据。"""
    from .deps import user_view

    return user_view(user)


@router.get("/directory")
def directory(q: str = Query("", max_length=64),
              types: str | None = Query(None, description="逗号分隔：user,role,dept,company,group"),
              role_code: str | None = Query(None, max_length=128),
              limit: int = Query(200, ge=1, le=500),
              user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """@ 选择器数据源：按关键字搜 用户/角色/组织架构/公司/用户组（可搜索、多选）。"""
    tlist = [t.strip() for t in types.split(",")] if types else None
    return identity_svc.search_directory(q, tlist, limit, role_code=role_code)


@router.get("/tree")
def tree(q: str = Query("", max_length=64),
         user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """组织架构树（树选模式）：部门层级 + 用户挂载 + 子树人数；q 过滤保留祖先链并平铺命中用户。"""
    return identity_svc.org_tree(q)


class TargetIn(BaseModel):
    type: str  # user|role|dept|company|group
    id: str
    label: str | None = None


class ExpandIn(BaseModel):
    targets: list[TargetIn]


@router.post("/expand")
def expand(body: ExpandIn, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """@ 目标 → 具体用户 id 集合（AI 派发/追加人员前解析范围用）。"""
    return identity_svc.expand_targets([t.model_dump() for t in body.targets])
