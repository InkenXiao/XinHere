"""MCP 服务器 HTTP 客户端构造：headers（鉴权等）经 httpx2.AsyncClient 注入。

mcp 2.x 的 streamable_http_client 不收 headers 参数，须自带 http_client。
"""
from __future__ import annotations


def http_client_of(server: dict):
    """无自定义 headers 返回 None（用 SDK 默认客户端），有则注入 header 的 AsyncClient。"""
    headers = server.get("headers") or {}
    if not headers:
        return None
    import httpx2

    return httpx2.AsyncClient(headers=dict(headers))
