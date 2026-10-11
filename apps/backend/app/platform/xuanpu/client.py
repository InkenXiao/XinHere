"""XuanPu (玄圃) 平台异步通信客户端与大文件流式交割服务"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx

from ...core.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_CALL_TIMEOUT_S = 60.0
_DEFAULT_UPLOAD_TIMEOUT_S = 180.0


class XuanPuClient:
    """封装与 XuanPu FastMCP 协议及 pro-cowork REST 的原生协程交互"""

    def __init__(
        self,
        mcp_url: Optional[str] = None,
        api_url: Optional[str] = None,
    ) -> None:
        self.mcp_url = (mcp_url or getattr(settings, "xuanpu_mcp_url", "http://127.0.0.1:8094/xuanpu/mcp")).rstrip("/")
        self.api_url = (api_url or getattr(settings, "xuanpu_api_url", "http://127.0.0.1:8091")).rstrip("/")

    async def check_health(self, timeout: float = 2.5) -> bool:
        """快速并发检查 XuanPu 核心服务是否就绪 (pro-cowork REST 8091 与 mcp-cowork 8094)"""
        try:
            async with httpx.AsyncClient(timeout=timeout, verify=False, trust_env=False) as client:
                res_api, res_mcp = await asyncio.gather(
                    client.get(f"{self.api_url}/api/meeting-records/?page=1&page_size=1"),
                    client.get(self.mcp_url, headers={"Accept": "text/event-stream"}),
                    return_exceptions=True,
                )
                if isinstance(res_api, Exception) or isinstance(res_mcp, Exception):
                    return False
                if res_api.status_code != 200:
                    return False
                # FastMCP SSE 端点在未带 session-id 时正常返回 400 或 406
                if res_mcp.status_code not in (200, 400, 406):
                    return False
                return True
        except Exception as exc:
            logger.warning("XuanPu 服务健康检查未通过: %s", exc)
            return False


    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_name: str = "",
        timeout: float = _DEFAULT_CALL_TIMEOUT_S,
    ) -> Any:
        """调用 XuanPu MCP 注册工具 (streamable-HTTP 标准 MCP 协议)

        透传用户身份至 X-User-Name 请求头，由 XuanPu 网关自动完成鉴权与权限映射。
        """
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        headers = {}
        if user_name:
            headers["X-User-Name"] = quote(user_name.strip())

        async def _invoke() -> Any:
            # 使用带身份头的 AsyncClient 发起 MCP 协商 (忽略自签证书与环境变量代理)
            async with httpx.AsyncClient(headers=headers, timeout=timeout, verify=False, trust_env=False) as hc:
                async with streamable_http_client(self.mcp_url, http_client=hc) as streams:
                    read_stream, write_stream = streams[0], streams[1]
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments or {})

            # 解析 MCP 响应内容
            parts: list[str] = []
            for item in (getattr(result, "content", None) or []):
                text_val = getattr(item, "text", None)
                if text_val:
                    parts.append(str(text_val))

            if not parts and getattr(result, "structuredContent", None):
                return result.structuredContent

            raw_text = "\n".join(parts).strip()

            is_err = getattr(result, "is_error", None)
            if is_err is None:
                is_err = getattr(result, "isError", False)
            if is_err:
                raise RuntimeError(f"XuanPu MCP [{tool_name}] 执行失败: {raw_text}")

            if not raw_text:
                return {}

            # 尝试作为 JSON 解析
            try:
                return json.loads(raw_text)
            except Exception:
                if any(err_kw in raw_text for err_kw in ("失败", "Error", "Exception", "HTTP 404", "HTTP 400")):
                    raise RuntimeError(f"XuanPu MCP [{tool_name}] 报错: {raw_text}")
                return raw_text

        try:
            return await asyncio.wait_for(_invoke(), timeout=timeout)
        except Exception as exc:
            logger.warning("XuanPu MCP 工具调用异常 (%s): %s，尝试通过 pro-cowork REST 直连兜底", tool_name, exc)
            rest_res = await self._try_rest_fallback(tool_name, arguments or {}, user_name, timeout)
            if rest_res is not None:
                return rest_res
            logger.error("XuanPu MCP 工具调用失败 (%s): %s", tool_name, exc)
            raise RuntimeError(f"XuanPu MCP [{tool_name}] 调用失败: {exc}") from exc

    async def _try_rest_fallback(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_name: str,
        timeout: float,
    ) -> Optional[Any]:
        """当 FastMCP 网关因网络/DNS 解析等内部故障异常时，对会议资产提供 pro-cowork REST 直连保障"""
        try:
            async with httpx.AsyncClient(timeout=timeout, verify=False, trust_env=False) as hc:
                if tool_name == "xuanpu_meetings_list":
                    params = {
                        "keyword": arguments.get("keyword", ""),
                        "user_code": arguments.get("user_code", "") or user_name,
                        "source": arguments.get("source", ""),
                        "page": arguments.get("page", 1),
                        "page_size": arguments.get("page_size", 10),
                    }
                    if arguments.get("project_id") is not None:
                        params["project_id"] = arguments["project_id"]
                    resp = await hc.get(f"{self.api_url}/api/meeting-records/", params=params)
                    if resp.status_code == 200:
                        logger.info("XuanPu REST 兜底查询会议列表成功")
                        return resp.json()

                elif tool_name == "xuanpu_meeting_get":
                    uid = arguments.get("meeting_uid")
                    if uid:
                        resp = await hc.get(f"{self.api_url}/api/meeting-records/{uid}")
                        if resp.status_code == 200:
                            logger.info("XuanPu REST 兜底获取会议详情成功: %s", uid)
                            return resp.json()
                        elif resp.status_code == 404:
                            return {"ok": False, "error": "会议不存在"}

                elif tool_name == "xuanpu_meeting_save":
                    resp = await hc.post(f"{self.api_url}/api/meeting-records/", json=arguments)
                    if resp.status_code == 200:
                        logger.info("XuanPu REST 兜底持久化会议资产成功: %s", arguments.get("meeting_uid"))
                        return resp.json()
                    else:
                        logger.warning("XuanPu REST 兜底持久化失败 HTTP %d: %s", resp.status_code, resp.text)

                elif tool_name == "xuanpu_meeting_delete":
                    uid = arguments.get("meeting_uid")
                    if uid:
                        resp = await hc.delete(f"{self.api_url}/api/meeting-records/{uid}")
                        if resp.status_code in (200, 404):
                            logger.info("XuanPu REST 兜底删除会议资产成功 (status=%s): %s", resp.status_code, uid)
                            return {"ok": True, "meeting_uid": uid}
        except Exception as rest_err:
            logger.warning("XuanPu REST 直连兜底亦失败 (%s): %s", tool_name, rest_err)
        return None

    async def upload_audio_stream(
        self,
        file_path: Path | str,
        meeting_uid: str,
        user_name: str = "",
        max_retries: int = 2,
    ) -> dict[str, Any]:
        """单请求 HTTP Multipart 流式上传音频到 XuanPu pro-cowork (MinIO 专用隔离路径)

        封装指数退避重试 (最多 2 次)，支持 10m~3h 大文件流式直传。
        """
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"待上传音频文件不存在: {p}")

        upload_url = f"{self.api_url}/api/meeting-records/audio"
        last_exc: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            try:
                with open(p, "rb") as audio_file_obj:
                    files = {
                        "file": (p.name, audio_file_obj, "audio/wav"),
                    }
                    data = {
                        "meeting_uid": meeting_uid,
                        "user_code": user_name,
                        "user_name": user_name,
                    }
                    headers = {}
                    if user_name:
                        headers["X-User-Name"] = quote(user_name.strip())

                    async with httpx.AsyncClient(timeout=_DEFAULT_UPLOAD_TIMEOUT_S, verify=False, trust_env=False) as client:
                        resp = await client.post(
                            upload_url,
                            files=files,
                            data=data,
                            headers=headers,
                        )

                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"XuanPu 音频直传失败 HTTP {resp.status_code}: {resp.text[:300]}"
                    )

                result = resp.json()
                logger.info(
                    "音频直传 XuanPu 成功 (uid=%s, size=%d, attempt=%d): %s",
                    meeting_uid,
                    p.stat().st_size,
                    attempt,
                    result.get("object_key"),
                )
                return result

            except Exception as exc:
                last_exc = exc
                if attempt < max_retries:
                    wait_seconds = 2 ** attempt
                    logger.warning(
                        "音频直传 XuanPu 失败，%d 秒后重试 (attempt %d/%d, uid=%s): %s",
                        wait_seconds,
                        attempt + 1,
                        max_retries,
                        meeting_uid,
                        exc,
                    )
                    await asyncio.sleep(wait_seconds)
                else:
                    logger.error(
                        "音频直传 XuanPu 最终失败 (uid=%s, attempts=%d): %s",
                        meeting_uid,
                        max_retries + 1,
                        exc,
                    )

        raise RuntimeError(f"音频直传 XuanPu 失败 (重试 {max_retries} 次): {last_exc}") from last_exc


_global_client: Optional[XuanPuClient] = None


def get_xuanpu_client() -> XuanPuClient:
    """获取全局 XuanPu 客户端单例"""
    global _global_client
    if _global_client is None:
        _global_client = XuanPuClient()
    return _global_client
