"""agent 外部 MCP 工具接入：AGENT_MCP_SERVERS 配置的 MCP 服务器工具并入对话工具集。

- 官方 mcp 2.x SDK（streamable http）直连，不引第三方适配器（生态里 mcp 1.x/2.x 分裂，
  langchain-mcp-adapters 当前只兼容 1.x，与 fastmcp 4 钉住的 mcp==2.1.1 冲突）；
- 每服务器独立加载（fail-soft：不可达只告警跳过，不阻断启动）；
- 每次调用独立连接（连/list_tools → call_tool → 断开），60s 超时；异常返回错误字符串
  供 LLM 如实转述；
- 工具名加前缀 mcp_<server>_<name>（防与内置工具撞名），参数 schema 动态转 pydantic，
  缓存进程级（graph 每轮重建不重连）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, create_model

from ...core.config import settings
from ...core.context import AuditCtx, reset_ctx, set_ctx
from ..scms.token_auth import current_user_token, identity_of

logger = logging.getLogger(__name__)

_CALL_TIMEOUT_S = 60
_cache: list[BaseTool] | None = None
_lock = threading.Lock()

_TYPE_MAP = {"string": (str, ...), "integer": (int, ...), "number": (float, ...),
             "boolean": (bool, ...), "array": (list, ...), "object": (dict, ...)}


def _parse_servers() -> list[dict]:
    """AGENT_MCP_SERVERS：JSON 数组 [{"name":"kb","url":"http://.../mcp","headers":{}}]。"""
    raw = (settings.agent_mcp_servers or "").strip()
    if not raw:
        return []
    try:
        arr = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AGENT_MCP_SERVERS 不是合法 JSON：{exc}") from exc
    out = []
    for i, e in enumerate(arr if isinstance(arr, list) else []):
        name, url = str(e.get("name") or "").strip(), str(e.get("url") or "").strip()
        if not name or not url:
            raise RuntimeError(f"AGENT_MCP_SERVERS[{i}] 缺 name/url")
        out.append({"name": name, "url": url, "headers": e.get("headers") or {}})
    return out


def _sanitize(name: str) -> str:
    return re.sub(r"\W+", "_", name)[:64]


def _schema_model(tool_name: str, schema: dict | None) -> type[BaseModel] | None:
    """远端 inputSchema（JSON Schema）→ pydantic 参数模型（LLM 据此规划参数）。"""
    props = (schema or {}).get("properties") or {}
    if not props:
        return None
    required = set((schema or {}).get("required") or [])
    fields: dict[str, tuple] = {}
    for k, p in props.items():
        t, _ = _TYPE_MAP.get(str(p.get("type") or "string"), (str, ...))
        default = None if k in required else (p.get("default") if "default" in p else None)
        fields[_sanitize(k) or f"arg_{len(fields)}"] = (
            t if k in required else (t | None), default)
    return create_model(f"{_sanitize(tool_name)}_args", **fields)


def _call_remote(server: dict, tool_name: str, arguments: dict) -> str:
    """单次连接完成一次工具调用；返回拼接文本（超长截断，防撑爆 LLM 上下文）。"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from .event_translator import TOOL_RESULT_MAX
    from .mcp_http import http_client_of

    async def _run() -> str:
        async with streamable_http_client(server["url"], http_client=http_client_of(server)) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as s:
                await s.initialize()
                res = await s.call_tool(tool_name, arguments or {})
        parts = []
        for c in (getattr(res, "content", None) or []):
            text = getattr(c, "text", None)
            if text:
                parts.append(str(text))
        if not parts and getattr(res, "structuredContent", None):
            parts.append(json.dumps(res.structuredContent, ensure_ascii=False))
        result = "\n".join(parts) or "（空结果）"
        if len(result) > TOOL_RESULT_MAX:
            # 与 tool/result 的 content_truncated 同量级封顶：保头部 + 截断标记（不默默截断）
            result = result[:TOOL_RESULT_MAX] + f"\n…（外部工具结果超长已截断，原文 {len(result)} 字符）"
        return result

    return asyncio.run(asyncio.wait_for(_run(), timeout=_CALL_TIMEOUT_S))


def _load_one(server: dict) -> list[BaseTool]:
    """连单个 MCP 服务器列出工具并包装；失败返回 []（fail-soft）。"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from .mcp_http import http_client_of

    async def _list() -> list[Any]:
        async with streamable_http_client(server["url"], http_client=http_client_of(server)) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as s:
                await s.initialize()
                return (await s.list_tools()).tools

    try:
        remote = asyncio.run(asyncio.wait_for(_list(), timeout=_CALL_TIMEOUT_S))
    except Exception as exc:
        logger.warning("MCP 服务器 %s（%s）不可达，跳过：%r", server["name"], server["url"], exc)
        return []

    prefix = _sanitize(f"mcp_{server['name']}")
    wrapped: list[BaseTool] = []
    for t in remote:
        base_name, base_desc = t.name or "tool", (t.description or "")[:800]
        full = f"{prefix}_{_sanitize(base_name)}"
        args_schema = _schema_model(full, getattr(t, "inputSchema", None))

        @tool(full, description=f"[外部 MCP {server['name']}] {base_desc}",
              args_schema=args_schema)
        def _sync_wrap(_srv=server, _tn=base_name, _fn=full, **kwargs):
            # 轻量审计：注入 MCP 审计上下文（channel=mcp，actor=模型+工具名），
            # 调用链内如发生 BusinessBase 访问，操作日志按 MCP 归因。MCP 工具不走
            # tool_scope（无 db 会话需求）；用户身份尽力取当前 token 缓存——agent run
            # 线程已在 run 级重绑 token，取不到时回落 system，不影响调用本身
            ident = identity_of(current_user_token())
            user_id = ident.user_id if ident else "system"
            audit = AuditCtx(
                user_id=user_id,
                channel="mcp",
                actor=f"{settings.main_model}+{_fn}",
                entry_point=_fn,
                detail_context={"context": {
                    "tool_call_arguments": kwargs,
                    "operator_user_id": user_id,
                }},
            )
            tok = set_ctx(audit)
            try:
                return _call_remote(_srv, _tn, kwargs)
            except Exception as exc:
                logger.warning("MCP 工具 %s 调用失败：%r", _fn, exc)
                return f"外部工具 {_fn} 调用失败：{exc}"
            finally:
                reset_ctx(tok)

        wrapped.append(_sync_wrap)
    logger.info("MCP 工具加载：%s → %d 个（%s）",
                server["name"], len(wrapped), ", ".join(t.name for t in wrapped))
    return wrapped


def load_mcp_tools() -> list[BaseTool]:
    """全量外部 MCP 工具（进程级缓存；未配置返回空列表）。"""
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is not None:
            return _cache
        tools: list[BaseTool] = []
        for server in _parse_servers():
            tools.extend(_load_one(server))
        _cache = tools
        return _cache


def reset_cache() -> None:
    """测试用：清缓存强制重载。"""
    global _cache
    with _lock:
        _cache = None
