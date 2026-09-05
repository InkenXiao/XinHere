"""XuanPu 平台数据通道 · 全部经 MCP 网关获取（禁直连 XuanPu 数据库）

链路: 本服务 → mcp-cowork /xuanpu/mcp (streamable-HTTP MCP) → pro-cowork REST。
身份: xinhere 用户 display_name 作 X-User-Name 头（URL 编码），
     XuanPu 全平台鉴权与数据权限与 Web 端一致。
"""
from __future__ import annotations

import json
from urllib.parse import quote

from ..core.config import settings
from .kb import McpClient


def _client(user_name: str, timeout: float = 30.0) -> McpClient:
    """按用户身份新建 MCP 客户端（X-User-Name 头透传中文姓名）"""
    return McpClient(
        settings.xuanpu_mcp_url,
        timeout=timeout,
        headers={"X-User-Name": quote(user_name or "", safe="")},
        verify=False,  # xuanpu Nginx 自签证书
    )


def call_tool(tool_name: str, arguments: dict, user_name: str,
              timeout: float = 30.0) -> dict:
    """调用 XuanPu MCP 网关工具并解析 JSON 结果；失败抛异常"""
    client = _client(user_name, timeout)
    text = client.call(tool_name, arguments)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _fail_msg(exc: Exception) -> str:
    return str(exc)[:200]


# ---------- 高层封装（agent 工具与 REST 代理共用） ----------

def list_agents(user_name: str) -> list[dict]:
    return call_tool("xuanpu_agents", {}, user_name)


def agent_chat(agent_id: int, message: str, user_name: str,
               session_id: int | None = None) -> dict:
    # debug 一轮含多轮 LLM 工具调用，给足余量
    return call_tool("xuanpu_agent_chat",
                     {"agent_id": agent_id, "message": message, "session_id": session_id},
                     user_name, timeout=320.0)


def todos(owner: str, user_name: str) -> dict:
    return call_tool("xuanpu_todos", {"owner": owner or ""}, user_name)


def todo_create(name: str, user_name: str, owner: str = "",
                week_start: str = "", week_end: str = "",
                priority: str = "medium", remark: str = "") -> dict:
    return call_tool("xuanpu_todo_create", {
        "name": name, "owner": owner, "week_start": week_start,
        "week_end": week_end, "priority": priority, "remark": remark,
    }, user_name)


def dashboard(user_name: str) -> dict:
    return call_tool("xuanpu_dashboard", {}, user_name, timeout=120.0)
