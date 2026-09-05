"""XuanPu 平台 REST 代理 · 前端待办/看板数据经 MCP 网关获取（禁直连 XuanPu 数据库）

链路: 前端 → 本服务 → mcp-cowork /xuanpu/mcp (MCP) → pro-cowork REST。
身份: Bearer token 解析 SysUser.display_name 作 X-User-Name（URL 编码）透传，
     XuanPu 数据权限与 Web 端一致。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import xuanpu as xuanpu_svc
from .deps import current_user

router = APIRouter(prefix="/xuanpu", tags=["xuanpu"])


def _identity(user: SysUser) -> str:
    return user.display_name or user.username


@router.get("/agents")
def agents(user: SysUser = Depends(current_user)):
    try:
        return {"items": xuanpu_svc.list_agents(_identity(user))}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 平台不可达：{exc}")


class AgentChatIn(BaseModel):
    agent_id: int
    message: str
    session_id: int | None = None


@router.post("/agents/chat")
def agent_chat(body: AgentChatIn, user: SysUser = Depends(current_user)):
    try:
        return xuanpu_svc.agent_chat(body.agent_id, body.message,
                                     _identity(user), session_id=body.session_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 智能体调用失败：{exc}")


@router.get("/todos")
def todos(owner: str = "", user: SysUser = Depends(current_user)):
    try:
        return xuanpu_svc.todos(owner, _identity(user))
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
def todo_create(body: TodoCreateIn, user: SysUser = Depends(current_user)):
    try:
        return xuanpu_svc.todo_create(
            body.name, _identity(user), owner=body.owner,
            week_start=body.week_start, week_end=body.week_end,
            priority=body.priority, remark=body.remark,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 创建待办失败：{exc}")


@router.get("/dashboard")
def dashboard(user: SysUser = Depends(current_user)):
    try:
        return xuanpu_svc.dashboard(_identity(user))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"XuanPu 平台不可达：{exc}")
