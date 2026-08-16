from __future__ import annotations

import json
import threading

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import errors
from ..core.config import settings
from ..persistence.models import KbSource


def list_sources(db: Session) -> list[dict]:
    rows = db.scalars(select(KbSource).order_by(KbSource.kb_id)).all()
    return [
        {"kb_id": r.kb_id, "name": r.name, "parent_id": r.parent_id, "kb_type": r.kb_type}
        for r in rows
    ]


class McpClient:
    """FastMCP streamable-HTTP 最小客户端；工具名运行时 tools/list 发现。"""

    def __init__(self, url: str, timeout: float = 15.0):
        self.url = url
        self.timeout = timeout
        self._session_id: str | None = None
        self._tools: list[dict] | None = None
        self._lock = threading.Lock()

    def _parse_response(self, resp: httpx.Response) -> dict:
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
            raise RuntimeError("MCP SSE 无 data 帧")
        return resp.json()

    def _post(self, payload: dict) -> dict:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.url, json=payload, headers=headers)
            resp.raise_for_status()
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._session_id = sid
            if resp.status_code == 202 or not resp.content:
                return {}
            return self._parse_response(resp)

    def _ensure_init(self) -> None:
        if self._session_id:
            return
        self._post(
            {
                "jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "xinhere-backend", "version": "1.0"},
                },
            }
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def tools_list(self) -> list[dict]:
        with self._lock:
            self._ensure_init()
            data = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
            self._tools = data.get("result", {}).get("tools", [])
            return self._tools

    def call(self, name: str, arguments: dict) -> str:
        with self._lock:
            self._ensure_init()
            data = self._post(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": name, "arguments": arguments}}
            )
        if "error" in data:
            raise RuntimeError(str(data["error"]))
        result = data.get("result", {})
        parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return "\n".join(p for p in parts if p)


def _pick_search_tool(tools: list[dict]) -> dict | None:
    """从 tools/list 结果挑检索工具：优先名字含 search/query/rag。"""
    for t in tools:
        name = t.get("name", "").lower()
        if any(k in name for k in ("search", "query", "rag", "retrieve")):
            return t
    return tools[0] if tools else None


def search(query: str, kb_id: str | None = None) -> list[dict]:
    """MCP 检索代理；不可达/失败 → UPSTREAM_ERROR。命中形状 {title, snippet, source}。"""
    client = McpClient(settings.kb_mcp_url)
    tools = client.tools_list()
    tool = _pick_search_tool(tools)
    if tool is None:
        raise errors.upstream("知识库无可用检索工具")
    props = tool.get("inputSchema", {}).get("properties", {})
    args: dict = {}
    for pname in props:
        low = pname.lower()
        if any(k in low for k in ("query", "keyword", "text", "q")):
            args[pname] = query
        elif any(k in low for k in ("kb", "collection", "dataset", "source")) and kb_id:
            args[pname] = kb_id
    if not args:
        args = {"query": query}
    text = client.call(tool["name"], args)
    try:
        parsed = json.loads(text)
        items = parsed if isinstance(parsed, list) else parsed.get("results", parsed.get("hits", []))
        hits = [
            {
                "title": str(it.get("title") or it.get("name") or "")[:120],
                "snippet": str(it.get("snippet") or it.get("content") or it.get("text") or "")[:300],
                "source": str(it.get("source") or it.get("kb") or kb_id or ""),
            }
            for it in items[:10]
        ]
        return hits
    except (json.JSONDecodeError, AttributeError):
        return [{"title": "知识库命中", "snippet": text[:300], "source": kb_id or ""}] if text else []
