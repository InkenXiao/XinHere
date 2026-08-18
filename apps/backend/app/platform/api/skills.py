from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import skills as skills_svc
from .deps import current_user

router = APIRouter(prefix="/skills", tags=["skills"])

logger = logging.getLogger(__name__)


@router.get("")
def list_skills(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """技能目录 + 当前用户启用态（首次访问懒播种默认 3 核心技能）。"""
    return {"items": skills_svc.catalog_for_user(db, user.user_id)}


class SetSkillIn(BaseModel):
    enabled: bool


@router.put("/{skill_key}")
def set_skill(skill_key: str, body: SetSkillIn,
              user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    logger.info("技能切换 user=%s skill=%s enabled=%s", user.username, skill_key, body.enabled)
    return skills_svc.set_enabled(db, user.user_id, skill_key, body.enabled)


@router.get("/{skill_key}/templates")
def list_templates(skill_key: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """技能模版列表（首次访问懒播种默认模版）。"""
    return {"items": skills_svc.templates_for(db, skill_key)}
