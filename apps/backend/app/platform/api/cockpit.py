"""Xin台配置 · 入口（cockpit_entries）+ 分组卡片（cockpit_groups / cockpit_cards）

配置表非业务数据：无 is_delete 软删（DELETE 为硬删），不走业务审计。
启动时幂等 seed 默认入口与三组卡片（AI工作台 / AI技能 / 业务系统），仅补缺失项、不改已有配置。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import errors
from ...persistence.models import (
    CockpitCard,
    CockpitEntry,
    CockpitGroup,
    SysUser,
)
from ...persistence.session import get_db
from .deps import current_user, require_hq

router = APIRouter(prefix="/cockpit", tags=["cockpit"])

# 初始入口：XuanPu 三子系统（entry_path 为站内路径，免登 to 白名单 /pro/ /rag/ /mcp/）
SEED_ENTRIES = [
    {"key": "pro", "name": "投后管理", "entry_path": "/pro/", "icon": "", "sort": 10},
    {"key": "rag", "name": "知识中心", "entry_path": "/rag/", "icon": "", "sort": 20},
    {"key": "mcp", "name": "智能协作", "entry_path": "/mcp/", "icon": "", "sort": 30},
]

# 分组卡片 seed：kind=link 页面跳转（link_url 可配）/ task 技能任务卡 / meeting 实时会议卡
SEED_GROUPS = [
    {"key": "workbench", "name": "AI工作台", "sort": 10},
    {"key": "skill", "name": "AI技能", "sort": 20},
    {"key": "system", "name": "业务系统", "sort": 30},
]

SEED_CARDS = [
    {"key": "minutes", "group_key": "workbench", "name": "实时纪要",
     "kind": "meeting", "sort": 10},
    {"key": "docgen", "group_key": "workbench", "name": "文档生成",
     "kind": "link",
     "link_url": "http://192.168.1.161:8003/workbench?redirect=http://192.168.1.161:9096/login",
     "sort": 20},
    {"key": "fill", "group_key": "workbench", "name": "信息填报",
     "kind": "task", "skill_name": "信息填报", "sort": 30},
    {"key": "risk-report", "group_key": "skill", "name": "财务风险报告",
     "kind": "task", "skill_name": "财务风险报告", "sort": 10},
    {"key": "post-invest-report", "group_key": "skill", "name": "投后管理报告",
     "kind": "task", "skill_name": "投后管理报告", "sort": 20},
    {"key": "fixed-income", "group_key": "skill", "name": "固收Skill",
     "kind": "task", "skill_name": "固收Skill", "sort": 30},
    {"key": "ops", "group_key": "system", "name": "运营管理系统",
     "kind": "link", "link_url": "http://192.168.1.161:9096/dashboard/AnnualEvaluation/index",
     "sort": 10},
    {"key": "qingshan-kb", "group_key": "system", "name": "青山知识库",
     "kind": "link", "link_url": "/rag/", "sort": 20},
    {"key": "tide-cowork", "group_key": "system", "name": "TIDE CoWork",
     "kind": "link", "link_url": "/mcp/", "sort": 30},
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


def seed_cards(db: Session) -> int:
    """幂等 seed 分组 + 卡片：仅补缺失 key。返回新增条数（调用方负责 commit）。"""
    added = 0
    groups = set(db.scalars(select(CockpitGroup.key)).all())
    for item in SEED_GROUPS:
        if item["key"] in groups:
            continue
        db.add(CockpitGroup(**item))
        added += 1
    cards = set(db.scalars(select(CockpitCard.key)).all())
    for item in SEED_CARDS:
        if item["key"] in cards:
            continue
        db.add(CockpitCard(**item))
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


# ---------- 分组卡片（Xin台主界面） ----------


def _card_view(c: CockpitCard) -> dict:
    return {
        "id": c.id,
        "key": c.key,
        "group_key": c.group_key,
        "name": c.name,
        "kind": c.kind,  # link / task / meeting
        "link_url": c.link_url,
        "skill_id": c.skill_id,
        "skill_name": c.skill_name,
        "icon": c.icon,
        "sort": c.sort,
        "enabled": c.enabled,
    }


@router.get("/cards")
def list_cards(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """分组卡片：启用分组 + 启用卡片（前端按 group_key 组装，每组一行三列）。"""
    groups = db.scalars(
        select(CockpitGroup).where(CockpitGroup.enabled.is_(True))
        .order_by(CockpitGroup.sort, CockpitGroup.id)
    ).all()
    cards = db.scalars(
        select(CockpitCard).where(CockpitCard.enabled.is_(True))
        .order_by(CockpitCard.sort, CockpitCard.id)
    ).all()
    by_group: dict[str, list[dict]] = {}
    for c in cards:
        by_group.setdefault(c.group_key, []).append(_card_view(c))
    return {
        "items": [
            {"key": g.key, "name": g.name, "sort": g.sort, "cards": by_group.get(g.key, [])}
            for g in groups
        ]
    }


class GroupIn(BaseModel):
    key: str
    name: str
    sort: int = 0
    enabled: bool = True


class GroupPatch(BaseModel):
    name: str | None = None
    sort: int | None = None
    enabled: bool | None = None


@router.post("/groups", status_code=201)
def create_group(body: GroupIn, user: SysUser = Depends(require_hq),
                 db: Session = Depends(get_db)):
    if db.scalars(select(CockpitGroup).where(CockpitGroup.key == body.key)).first():
        raise errors.validation("分组标识已存在")
    group = CockpitGroup(key=body.key, name=body.name, sort=body.sort, enabled=body.enabled)
    db.add(group)
    db.flush()
    return {"id": group.id, "key": group.key, "name": group.name,
            "sort": group.sort, "enabled": group.enabled}


@router.patch("/groups/{group_id}")
def update_group(group_id: int, body: GroupPatch, user: SysUser = Depends(require_hq),
                 db: Session = Depends(get_db)):
    group = db.get(CockpitGroup, group_id)
    if group is None:
        raise errors.not_found("分组不存在")
    for field in ("name", "sort", "enabled"):
        val = getattr(body, field)
        if val is not None:
            setattr(group, field, val)
    db.flush()
    return {"id": group.id, "key": group.key, "name": group.name,
            "sort": group.sort, "enabled": group.enabled}


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, user: SysUser = Depends(require_hq),
                 db: Session = Depends(get_db)):
    group = db.get(CockpitGroup, group_id)
    if group is None:
        raise errors.not_found("分组不存在")
    db.delete(group)  # 配置表：硬删（组内卡片保留，可改挂其他分组）
    db.flush()
    return {"ok": True}


class CardIn(BaseModel):
    key: str
    group_key: str
    name: str
    kind: str = "task"  # link / task / meeting
    link_url: str | None = None
    skill_id: int | None = None
    skill_name: str | None = None
    icon: str = ""
    sort: int = 0
    enabled: bool = True


class CardPatch(BaseModel):
    group_key: str | None = None
    name: str | None = None
    kind: str | None = None
    link_url: str | None = None
    skill_id: int | None = None
    skill_name: str | None = None
    icon: str | None = None
    sort: int | None = None
    enabled: bool | None = None


def _check_card(body: CardIn | CardPatch) -> None:
    if body.kind is not None and body.kind not in ("link", "task", "meeting"):
        raise errors.validation("卡片类型须为 link / task / meeting")
    if body.kind == "link" and not (body.link_url or "").strip():
        raise errors.validation("跳转卡片须配置跳转地址")


@router.post("/cards", status_code=201)
def create_card(body: CardIn, user: SysUser = Depends(require_hq),
                db: Session = Depends(get_db)):
    _check_card(body)
    if db.scalars(select(CockpitCard).where(CockpitCard.key == body.key)).first():
        raise errors.validation("卡片标识已存在")
    card = CockpitCard(
        key=body.key, group_key=body.group_key, name=body.name, kind=body.kind,
        link_url=body.link_url, skill_id=body.skill_id, skill_name=body.skill_name,
        icon=body.icon, sort=body.sort, enabled=body.enabled,
    )
    db.add(card)
    db.flush()
    return _card_view(card)


@router.patch("/cards/{card_id}")
def update_card(card_id: int, body: CardPatch, user: SysUser = Depends(require_hq),
                db: Session = Depends(get_db)):
    card = db.get(CockpitCard, card_id)
    if card is None:
        raise errors.not_found("卡片不存在")
    _check_card(body)
    kind = body.kind or card.kind
    if kind == "link" and not (body.link_url or card.link_url or "").strip():
        raise errors.validation("跳转卡片须配置跳转地址")
    for field in ("group_key", "name", "kind", "link_url", "skill_id",
                  "skill_name", "icon", "sort", "enabled"):
        val = getattr(body, field)
        if val is not None:
            setattr(card, field, val)
    db.flush()
    return _card_view(card)


@router.delete("/cards/{card_id}")
def delete_card(card_id: int, user: SysUser = Depends(require_hq),
                db: Session = Depends(get_db)):
    card = db.get(CockpitCard, card_id)
    if card is None:
        raise errors.not_found("卡片不存在")
    db.delete(card)  # 配置表：硬删
    db.flush()
    return {"ok": True}
