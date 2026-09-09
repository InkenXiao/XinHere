"""XuanPu 平台 REST 代理 · 前端待办/看板/技能/填报数据经 MCP 网关获取（禁直连 XuanPu 数据库）

链路: 前端 → 本服务 → mcp-cowork /xuanpu/mcp (MCP) → pro-cowork REST。
身份: 双轨鉴权 —— 用户 token 行有未过期的 xuanpu_token 时走 Bearer 头
     （与 XuanPu Web 端同权, 含待办/填报等业务数据）；
     否则回落 X-User-Name 头（display_name URL 编码, 仅公开数据）。
"""
from __future__ import annotations

import json
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import errors
from ...core.config import settings
from ...persistence.models import CockpitCard, CockpitEntry, SysUser
from ...persistence.session import get_db
from ...services import xuanpu as xuanpu_svc
from .deps import current_user

router = APIRouter(prefix="/xuanpu", tags=["xuanpu"])


def _mcp_ctx(db: Session, user: SysUser) -> tuple[str, str | None]:
    """MCP 调用身份：display_name + 网关 token（无/过期为 None，回落 X-User-Name）。"""
    return user.display_name or user.username, xuanpu_svc.resolve_token(db, user.user_id)


@router.get("/agents")
def agents(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    identity, xtoken = _mcp_ctx(db, user)
    try:
        return {"items": xuanpu_svc.list_agents(identity, xuanpu_token=xtoken)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 平台不可达：{exc}")


class AgentChatIn(BaseModel):
    agent_id: int
    message: str
    session_id: int | None = None


@router.post("/agents/chat")
def agent_chat(body: AgentChatIn, user: SysUser = Depends(current_user),
               db: Session = Depends(get_db)):
    identity, xtoken = _mcp_ctx(db, user)
    try:
        return xuanpu_svc.agent_chat(body.agent_id, body.message, identity,
                                      session_id=body.session_id, xuanpu_token=xtoken)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 智能体调用失败：{exc}")


@router.get("/todos")
def todos(owner: str = "", user: SysUser = Depends(current_user),
          db: Session = Depends(get_db)):
    identity, xtoken = _mcp_ctx(db, user)
    try:
        return xuanpu_svc.todos(owner, identity, xuanpu_token=xtoken)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 平台不可达：{exc}")


class TodoCreateIn(BaseModel):
    name: str
    owner: str = ""
    week_start: str = ""
    week_end: str = ""
    priority: str = "medium"
    remark: str = ""


@router.post("/todos")
def todo_create(body: TodoCreateIn, user: SysUser = Depends(current_user),
                db: Session = Depends(get_db)):
    identity, xtoken = _mcp_ctx(db, user)
    try:
        return xuanpu_svc.todo_create(
            body.name, identity, owner=body.owner,
            week_start=body.week_start, week_end=body.week_end,
            priority=body.priority, remark=body.remark, xuanpu_token=xtoken,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 创建待办失败：{exc}")


@router.get("/dashboard")
def dashboard(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    identity, xtoken = _mcp_ctx(db, user)
    try:
        return xuanpu_svc.dashboard(identity, xuanpu_token=xtoken)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 平台不可达：{exc}")


# ---------- 技能（Xin台技能直跑） ----------


@router.get("/skills")
def skills(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    identity, xtoken = _mcp_ctx(db, user)
    try:
        return {"items": xuanpu_svc.skills(identity, xuanpu_token=xtoken)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 平台不可达：{exc}")


class SkillRunIn(BaseModel):
    inputs: dict = {}


@router.post("/skills/{skill_id}/run")
def skill_run(skill_id: int, body: SkillRunIn,
              user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    identity, xtoken = _mcp_ctx(db, user)
    try:
        return xuanpu_svc.skill_run(str(skill_id), json.dumps(body.inputs, ensure_ascii=False),
                                     identity, xuanpu_token=xtoken)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 技能运行失败：{exc}")


# ---------- 填报任务（待办「去填报」弹窗） ----------


@router.get("/fill/template")
def fill_template(template_id: int, user: SysUser = Depends(current_user),
                  db: Session = Depends(get_db)):
    identity, xtoken = _mcp_ctx(db, user)
    try:
        return xuanpu_svc.fill_template_detail(template_id, identity, xuanpu_token=xtoken)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 平台不可达：{exc}")


@router.get("/fill/assignment")
def fill_assignment(assignment_id: int, user: SysUser = Depends(current_user),
                    db: Session = Depends(get_db)):
    identity, xtoken = _mcp_ctx(db, user)
    try:
        items = xuanpu_svc.fill_my_assignments(identity, xuanpu_token=xtoken)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 平台不可达：{exc}")
    if isinstance(items, dict):  # 网关异常返回 {"raw": ...} 时的容错
        items = items.get("items") or []
    for a in items:
        if a.get("assignment_id") == assignment_id:
            return a
    raise errors.not_found("填报任务不存在或不在您的名下")


class FillDataIn(BaseModel):
    assignment_id: int
    data: dict = {}


@router.post("/fill/draft")
def fill_draft(body: FillDataIn, user: SysUser = Depends(current_user),
               db: Session = Depends(get_db)):
    identity, xtoken = _mcp_ctx(db, user)
    try:
        return xuanpu_svc.fill_draft(body.assignment_id,
                                      json.dumps(body.data, ensure_ascii=False),
                                      identity, xuanpu_token=xtoken)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 保存草稿失败：{exc}")


@router.post("/fill/submit")
def fill_submit(body: FillDataIn, user: SysUser = Depends(current_user),
                db: Session = Depends(get_db)):
    identity, xtoken = _mcp_ctx(db, user)
    try:
        return xuanpu_svc.fill_submit(body.assignment_id,
                                       json.dumps(body.data, ensure_ascii=False),
                                       identity, xuanpu_token=xtoken)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 填报提交失败：{exc}")


# ---------- Xin台免登跳转 ----------


class LaunchIn(BaseModel):
    key: str  # cockpit_entries.key


def _sso_launch(user: SysUser, path: str) -> dict:
    """XuanPu 站内路径免登跳转：有统一身份绑定 → 换一次性 ticket；无 → 直连（首登提示）。"""
    target = f"{settings.xuanpu_public_url}{path}"
    if not user.xuanpu_user_id:
        # 本地兜底账号未绑定统一身份：返回直连地址，首次需在 XuanPu 登录一次
        return {"url": target, "first_login": True}
    try:
        resp = httpx.post(
            settings.sso_ticket_url,
            json={
                "client_id": settings.sso_client_id,
                "client_secret": settings.sso_client_secret,
                "user_code": user.username,
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise errors.upstream(f"统一身份服务不可达：{exc}") from exc
    if resp.status_code != 200:
        raise errors.upstream("免登票据签发失败")
    ticket = (resp.json() or {}).get("ticket") or ""
    if not ticket:
        raise errors.upstream("免登票据签发失败")
    url = (
        f"{settings.xuanpu_public_url}/sso/launch"
        f"?ticket={quote(ticket, safe='')}&to={quote(path, safe='/')}"
    )
    return {"url": url, "first_login": False}


@router.post("/launch")
def launch(body: LaunchIn, user: SysUser = Depends(current_user),
           db: Session = Depends(get_db)):
    """系统卡跳转：有统一身份绑定 → 换一次性 ticket 免登；无 → 外网直连（首登提示）。"""
    entry = db.scalars(
        select(CockpitEntry).where(CockpitEntry.key == body.key, CockpitEntry.enabled.is_(True))
    ).first()
    if entry is None:
        raise errors.not_found("入口不存在或已停用")
    return _sso_launch(user, entry.entry_path)


class CardOpenIn(BaseModel):
    key: str  # cockpit_cards.key


@router.post("/cards/open")
def open_card(body: CardOpenIn, user: SysUser = Depends(current_user),
              db: Session = Depends(get_db)):
    """Xin台卡片跳转：站内路径（/ 开头）走免登；绝对 URL 外链直接打开。"""
    card = db.scalars(
        select(CockpitCard)
        .where(CockpitCard.key == body.key, CockpitCard.enabled.is_(True))
    ).first()
    if card is None:
        raise errors.not_found("卡片不存在或已停用")
    url = (card.link_url or "").strip()
    if not url:
        raise errors.validation("卡片未配置跳转地址")
    if url.startswith("/"):
        return _sso_launch(user, url)
    return {"url": url, "first_login": False}
