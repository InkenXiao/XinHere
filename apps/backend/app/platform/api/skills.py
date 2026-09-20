"""本地文件夹技能列表：读取 backend/skills/<key>/SKILL.md 元数据，供对话输入区技能弹层展示。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...persistence.models import SysUser
from ...services import skill_files
from .deps import current_user

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("")
def list_local_skills(user: SysUser = Depends(current_user)):
    items = [
        {
            "skill_key": s["skill_key"],
            "name": s["name"],
            "desc": s["desc"],
            "sort_no": s["sort_no"],
        }
        for s in skill_files.list_file_skills()
    ]
    return {"items": items}
