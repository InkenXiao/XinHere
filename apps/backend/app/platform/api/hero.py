"""Xin语页用户自定义卡片 · hero_cards（链接 / 技能，按用户维度，逻辑删除）"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import errors
from ...persistence.models import HeroCard, SysUser
from ...persistence.session import get_db
from .deps import current_user

router = APIRouter(prefix="/hero", tags=["hero"])


def _view(c: HeroCard) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "kind": c.kind,  # link / skill
        "link_url": c.link_url,
        "skill_id": c.skill_id,
        "skill_name": c.skill_name,
        "sort": c.sort,
    }


def _get_card(db: Session, user: SysUser, card_id: str) -> HeroCard:
    c = db.get(HeroCard, card_id)
    if c is None or c.user_id != user.user_id or c.is_delete:
        raise errors.not_found("卡片不存在")
    return c


@router.get("/cards")
def list_cards(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(HeroCard).where(
            HeroCard.user_id == user.user_id,
            HeroCard.is_delete.is_(False),
        ).order_by(HeroCard.sort, HeroCard.created_at)
    ).all()
    return {"items": [_view(r) for r in rows]}


class HeroCardIn(BaseModel):
    name: str
    kind: str = "link"  # link / skill
    link_url: str | None = None
    skill_id: int | None = None
    skill_name: str | None = None
    sort: int = 0


def _check(body: HeroCardIn | "HeroCardPatch") -> None:
    if body.kind is not None and body.kind not in ("link", "skill"):
        raise errors.validation("卡片类型须为 link / skill")


@router.post("/cards", status_code=201)
def create_card(body: HeroCardIn, user: SysUser = Depends(current_user),
                db: Session = Depends(get_db)):
    _check(body)
    if body.kind == "link" and not (body.link_url or "").strip():
        raise errors.validation("请填写跳转地址")
    if body.kind == "skill" and not (body.skill_id or body.skill_name):
        raise errors.validation("请选择技能")
    card = HeroCard(
        user_id=user.user_id,
        name=body.name.strip()[:60] or "新卡片",
        kind=body.kind, link_url=body.link_url,
        skill_id=body.skill_id, skill_name=body.skill_name,
        sort=body.sort,
    )
    db.add(card)
    db.flush()
    return _view(card)


class HeroCardPatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    link_url: str | None = None
    skill_id: int | None = None
    skill_name: str | None = None
    sort: int | None = None


@router.patch("/cards/{card_id}")
def update_card(card_id: str, body: HeroCardPatch,
                user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    card = _get_card(db, user, card_id)
    _check(body)
    kind = body.kind or card.kind
    if kind == "link" and not (body.link_url or card.link_url or "").strip():
        raise errors.validation("请填写跳转地址")
    if kind == "skill" and not (body.skill_id or card.skill_id
                                or body.skill_name or card.skill_name):
        raise errors.validation("请选择技能")
    for field in ("name", "kind", "link_url", "skill_id", "skill_name", "sort"):
        val = getattr(body, field)
        if val is not None:
            setattr(card, field, val)
    if body.name is not None:
        card.name = body.name.strip()[:60] or card.name
    db.flush()
    return _view(card)


@router.delete("/cards/{card_id}")
def delete_card(card_id: str, user: SysUser = Depends(current_user),
                db: Session = Depends(get_db)):
    card = _get_card(db, user, card_id)
    card.is_delete = True  # 业务表：逻辑删除
    db.flush()
    return {"ok": True}
