"""XuanPu 平台数据通道 · 全部经 MCP 网关获取（禁直连 XuanPu 数据库）

链路: 本服务 → mcp-cowork /xuanpu/mcp (streamable-HTTP MCP) → pro-cowork REST。
身份: 双轨鉴权 —— 用户 token 行有未过期的 xuanpu_token 时走 Bearer 头
     （与 XuanPu Web 端同权, 含待办/填报等业务数据）；
     否则回落 X-User-Name 头（display_name URL 编码, 仅公开数据）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..persistence.models import SysAuthToken
from .kb import McpClient


def resolve_token(db: Session, user_id: str) -> str | None:
    """取用户当前有效的 XuanPu 网关 token；无或已过期返回 None（回落 X-User-Name）。"""
    now = datetime.now(timezone.utc)
    rows = db.scalars(select(SysAuthToken).where(SysAuthToken.user_id == user_id)).all()
    for row in rows:
        if row.xuanpu_token and row.xuanpu_token_exp and row.xuanpu_token_exp > now:
            return row.xuanpu_token
    return None


def _client(user_name: str, timeout: float = 30.0,
            xuanpu_token: str | None = None) -> McpClient:
    """按用户身份新建 MCP 客户端（双轨鉴权：Bearer 优先，回落 X-User-Name）"""
    if xuanpu_token:
        headers = {"Authorization": f"Bearer {xuanpu_token}"}
    else:
        headers = {"X-User-Name": quote(user_name or "", safe="")}
    return McpClient(
        settings.xuanpu_mcp_url,
        timeout=timeout,
        headers=headers,
        verify=False,  # xuanpu Nginx 自签证书
    )


def call_tool(tool_name: str, arguments: dict, user_name: str,
              timeout: float = 30.0, xuanpu_token: str | None = None) -> dict:
    """调用 XuanPu MCP 网关工具并解析 JSON 结果；失败抛异常"""
    client = _client(user_name, timeout, xuanpu_token)
    text = client.call(tool_name, arguments)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _fail_msg(exc: Exception) -> str:
    return str(exc)[:200]


# ---------- 高层封装（agent 工具与 REST 代理共用；xuanpu_token 均可缺省回落） ----------

def list_agents(user_name: str, xuanpu_token: str | None = None) -> list[dict]:
    return call_tool("xuanpu_agents", {}, user_name, xuanpu_token=xuanpu_token)


def agent_chat(agent_id: int, message: str, user_name: str,
               session_id: int | None = None,
               xuanpu_token: str | None = None) -> dict:
    # debug 一轮含多轮 LLM 工具调用，给足余量
    return call_tool("xuanpu_agent_chat",
                     {"agent_id": agent_id, "message": message, "session_id": session_id},
                     user_name, timeout=320.0, xuanpu_token=xuanpu_token)


def todos(owner: str, user_name: str, xuanpu_token: str | None = None) -> dict:
    return call_tool("xuanpu_todos", {"owner": owner or ""}, user_name,
                     xuanpu_token=xuanpu_token)


def todo_create(name: str, user_name: str, owner: str = "",
                week_start: str = "", week_end: str = "",
                priority: str = "medium", remark: str = "",
                xuanpu_token: str | None = None) -> dict:
    return call_tool("xuanpu_todo_create", {
        "name": name, "owner": owner, "week_start": week_start,
        "week_end": week_end, "priority": priority, "remark": remark,
    }, user_name, xuanpu_token=xuanpu_token)


def dashboard(user_name: str, xuanpu_token: str | None = None) -> dict:
    return call_tool("xuanpu_dashboard", {}, user_name, timeout=120.0,
                     xuanpu_token=xuanpu_token)


# ---------- 技能 / 平台工具 / MCP 服务 / IM 通道 ----------

def skills(user_name: str, category: str = "",
           xuanpu_token: str | None = None) -> list[dict]:
    return call_tool("xuanpu_skills", {"category": category or ""}, user_name,
                     xuanpu_token=xuanpu_token)


def skill_run(skill: str, input_data: str, user_name: str,
              xuanpu_token: str | None = None) -> dict:
    return call_tool("xuanpu_skill_run",
                     {"skill": skill, "input_data": input_data or "{}"},
                     user_name, timeout=320.0, xuanpu_token=xuanpu_token)


def platform_tools(user_name: str, xuanpu_token: str | None = None) -> list[dict]:
    return call_tool("xuanpu_tools", {}, user_name, xuanpu_token=xuanpu_token)


def tool_run(tool: str, arguments: str, user_name: str,
             xuanpu_token: str | None = None) -> dict:
    return call_tool("xuanpu_tool_run",
                     {"tool": tool, "arguments": arguments or "{}"},
                     user_name, timeout=320.0, xuanpu_token=xuanpu_token)


def mcp_servers(user_name: str, xuanpu_token: str | None = None) -> list[dict]:
    return call_tool("xuanpu_mcp_servers", {}, user_name, xuanpu_token=xuanpu_token)


def mcp_call(server: str, tool: str, arguments: str, user_name: str,
             xuanpu_token: str | None = None) -> dict:
    return call_tool("xuanpu_mcp_call",
                     {"server": server, "tool": tool, "arguments": arguments or "{}"},
                     user_name, timeout=320.0, xuanpu_token=xuanpu_token)


def im_channels(user_name: str, xuanpu_token: str | None = None) -> list[dict]:
    return call_tool("xuanpu_im_channels", {}, user_name, xuanpu_token=xuanpu_token)


def im_send(message: str, user_name: str, channel: str = "",
            xuanpu_token: str | None = None) -> dict:
    return call_tool("xuanpu_im_send",
                     {"message": message, "channel": channel or ""},
                     user_name, timeout=120.0, xuanpu_token=xuanpu_token)


def im_messages(user_name: str, channel: str = "",
                xuanpu_token: str | None = None) -> dict:
    return call_tool("xuanpu_im_messages", {"channel": channel or ""},
                     user_name, timeout=120.0, xuanpu_token=xuanpu_token)


# ---------- 填报任务 / 知识检索（Bearer 通道能力） ----------

def fill_create(title: str, fields: str, user_name: str, assignees: str = "",
                deadline: str = "", description: str = "",
                xuanpu_token: str | None = None) -> dict:
    """发起填报任务；fields 为字段 JSON 数组字符串（[{key,label,type,options?,required?}]），
    assignees 为责任人数组 JSON 字符串或逗号分隔姓名。"""
    return call_tool("xuanpu_fill_create", {
        "title": title, "fields": fields, "assignees": assignees or "",
        "deadline": deadline or "", "description": description or "",
    }, user_name, xuanpu_token=xuanpu_token)


def fill_templates(user_name: str, xuanpu_token: str | None = None) -> list[dict]:
    return call_tool("xuanpu_fill_templates", {}, user_name, xuanpu_token=xuanpu_token)


def fill_template_detail(template_id: int, user_name: str,
                         xuanpu_token: str | None = None) -> dict:
    return call_tool("xuanpu_fill_template_detail", {"template_id": template_id},
                     user_name, xuanpu_token=xuanpu_token)


def fill_my_assignments(user_name: str, xuanpu_token: str | None = None) -> dict:
    return call_tool("xuanpu_fill_my_assignments", {}, user_name,
                     xuanpu_token=xuanpu_token)


def fill_draft(assignment_id: int, data: str, user_name: str,
               xuanpu_token: str | None = None) -> dict:
    return call_tool("xuanpu_fill_draft",
                     {"assignment_id": assignment_id, "data": data or "{}"},
                     user_name, xuanpu_token=xuanpu_token)


def fill_submit(assignment_id: int, data: str, user_name: str,
                xuanpu_token: str | None = None) -> dict:
    return call_tool("xuanpu_fill_submit",
                     {"assignment_id": assignment_id, "data": data or "{}"},
                     user_name, xuanpu_token=xuanpu_token)


def kb_search(query: str, user_name: str, sources: str = "", top_k: int = 5,
              xuanpu_token: str | None = None) -> dict:
    return call_tool("xuanpu_kb_search",
                     {"query": query, "sources": sources or "", "top_k": top_k},
                     user_name, xuanpu_token=xuanpu_token)

def meeting_create(user_name: str, xuanpu_token: str | None = None,
                   title: str = "", meet_date: str = "", meet_time: str = "",
                   place: str = "", host: str = "", attendees: str = "",
                   description: str = "") -> dict:
    """创建 XuanPu 会议记录 (会议纪要落库, description 存纪要全文)

    优先走网关直连工具; 未注册时回退 xuanpu_tool_run (平台工具注册中心)。
    """
    args = {k: v for k, v in {
        "title": title, "meet_date": meet_date, "meet_time": meet_time,
        "place": place, "host": host, "attendees": attendees,
        "description": description,
    }.items() if v}
    if not args.get("title"):
        raise ValueError("会议主题不能为空")
    # 优先网关直连工具; 未注册 (返回 Unknown tool 文本) 时回退 xuanpu_tool_run (平台工具注册中心)
    try:
        direct = call_tool("create_meeting", args, user_name, timeout=60.0, xuanpu_token=xuanpu_token)
        if not (isinstance(direct, dict) and "Unknown tool" in str(direct.get("raw", ""))):
            return direct
    except Exception:  # noqa: BLE001
        pass
    res = tool_run("create_meeting", json.dumps(args, ensure_ascii=False),
                   user_name, xuanpu_token=xuanpu_token)
    if isinstance(res, dict) and res.get("ok") and isinstance(res.get("body"), dict):
        return res["body"]
    if isinstance(res, dict) and (res.get("error") or res.get("raw")):
        raise RuntimeError(str(res.get("error") or res.get("raw"))[:200])
    return res


async def audio_transcribe(user_name: str, xuanpu_token: str,
                           filename: str, content_type: str, raw: bytes) -> dict:
    """语音转写代理: 转发 xuanpu pro-cowork 实时转写接口 (POST 需 Bearer, 双轨仅 GET 放行)"""
    import httpx

    from ..core.config import settings

    url = settings.xuanpu_api_url.rstrip("/") + "/api/realtime-minutes/transcribe"
    files = {"file": (filename or "clip.webm", raw, content_type or "audio/webm")}
    data = {"mode": ""}
    async with httpx.AsyncClient(timeout=120, verify=False) as cli:
        resp = await cli.post(url, headers={"Authorization": f"Bearer {xuanpu_token}"},
                              files=files, data=data)
    if resp.status_code != 200:
        raise RuntimeError(f"转写服务返回 {resp.status_code}")
    return resp.json()
