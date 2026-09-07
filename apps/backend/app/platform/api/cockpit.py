"""Xin台入口配置 · cockpit_entries（launch 跳转目标）

配置表非业务数据：无 is_delete 软删（DELETE 为硬删），不走业务审计。
启动时幂等 seed pro/rag/mcp 三条默认入口，仅补缺失项、不改已有配置。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import errors
from ...persistence.models import CockpitEntry, SysUser
from ...persistence.session import get_db
from .deps import current_user, require_hq

router = APIRouter(prefix="/cockpit", tags=["cockpit"])

# 初始入口：XuanPu 三子系统（entry_path 为站内路径，免登 to 白名单 /pro/ /rag/ /mcp/）
SEED_ENTRIES = [
    {"key": "pro", "name": "投后管理", "entry_path": "/pro/", "icon": "", "sort": 10},
    {"key": "rag", "name": "知识中心", "entry_path": "/rag/", "icon": "", "sort": 20},
    {"key": "mcp", "name": "智能协作", "entry_path": "/mcp/", "icon": "", "sort": 30},
]


def seed_entries(db: Session) -> int:
    """幂等 seed：仅补缺失 key。返回新增条数（调用方负责 commit）。"""
    existing = set(db.scalars(select(CockpitEntry.key)).all())
    added = 0
    for item in SEED_ENTRIES:
        if item["key"] in existing:
            continue
        db.add(CockpitEntry(**item))
        added += 1
    if added:
        db.flush()
    return added


def _view(e: CockpitEntry) -> dict:
    return {
        "id": e.id,
        "key": e.key,
        "name": e.name,
        "entry_path": e.entry_path,
        "icon": e.icon,
        "sort": e.sort,
        "enabled": e.enabled,
    }


@router.get("/entries")
def list_entries(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(CockpitEntry).order_by(CockpitEntry.sort, CockpitEntry.id)).all()
    return {"items": [_view(r) for r in rows]}


class EntryIn(BaseModel):
    key: str
    name: str
    entry_path: str
    icon: str = ""
    sort: int = 0
    enabled: bool = True


class EntryPatch(BaseModel):
    name: str | None = None
    entry_path: str | None = None
    icon: str | None = None
    sort: int | None = None
    enabled: bool | None = None


@router.post("/entries", status_code=201)
def create_entry(body: EntryIn, user: SysUser = Depends(require_hq),
                 db: Session = Depends(get_db)):
    if not body.entry_path.startswith("/"):
        raise errors.validation("入口路径须以 / 开头（如 /pro/）")
    if db.scalars(select(CockpitEntry).where(CockpitEntry.key == body.key)).first():
        raise errors.validation("入口标识已存在")
    entry = CockpitEntry(
        key=body.key, name=body.name, entry_path=body.entry_path,
        icon=body.icon, sort=body.sort, enabled=body.enabled,
    )
    db.add(entry)
    db.flush()
    return _view(entry)


@router.patch("/entries/{entry_id}")
def update_entry(entry_id: int, body: EntryPatch, user: SysUser = Depends(require_hq),
                 db: Session = Depends(get_db)):
    entry = db.get(CockpitEntry, entry_id)
    if entry is None:
        raise errors.not_found("入口不存在")
    if body.entry_path is not None and not body.entry_path.startswith("/"):
        raise errors.validation("入口路径须以 / 开头（如 /pro/）")
    for field in ("name", "entry_path", "icon", "sort", "enabled"):
        val = getattr(body, field)
        if val is not None:
            setattr(entry, field, val)
    db.flush()
    return _view(entry)


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: int, user: SysUser = Depends(require_hq),
                 db: Session = Depends(get_db)):
    entry = db.get(CockpitEntry, entry_id)
    if entry is None:
        raise errors.not_found("入口不存在")
    db.delete(entry)  # 配置表：硬删
    db.flush()
    return {"ok": True}
