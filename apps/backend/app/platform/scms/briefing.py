"""信息简报出站流式代理（SCMS ai-session-service，SSE）。

上游为老前端（AAAAA-ai-chat 企业简报页）同一接口：
POST {SCMS_AI_BACKEND}ai-api/api/v1/ai/briefing/monthly/stream
- body JSON：{"year": int, "month": int}（简报数据口径必须传参）
- 请求头：Content-Type: application/json + X-SCMS-Access-Token: <用户 accessToken>
  （2026-09-12 实测：SCMS :9096 的 /ai-api 网关校验 X-SCMS-Access-Token——
  只带 Authorization: Bearer 返回 401 "Missing X-SCMS-Access-Token header"；
  老前端能只发 Bearer 是因其走自有 nginx /api 转发层做了头转换。
  与 tools.py nl2sql / 问数流出站同约定；token 由调用方显式传入——
  contextvars 不跨流式生成器，见 api/briefing.py）
- 响应 SSE：data 帧 JSON 兼容 chunk_type（text/complete）与 event_type
  （streaming_content/error/done）两种事件格式，由前端统一解析；本层不做语义
  解析，逐帧直通（上游错误帧原样下发，前端按 user_message ?? message 提示）。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Iterator

from ...core.config import settings

logger = logging.getLogger(__name__)


def stream_briefing(year: int, month: int, token: str) -> Iterator[dict]:
    """同步生成器：逐行读上游 SSE，提取 data 帧载荷原样 yield。

    EventSourceResponse 经 iterate_in_threadpool 支持同步生成器（与 tools.py
    的 urllib 阻塞调用同线程模型）；HTTP/连接错误统一 RuntimeError（携带可
    展示的中文提示），由路由层转 error 帧下发前端。
    """
    url = settings.scms_ai_backend.rstrip('/') + "/ai-api/api/v1/ai/briefing/monthly/stream"
    req = urllib.request.Request(
        url,
        data=json.dumps({"year": year, "month": month}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-SCMS-Access-Token": token,
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    logger.info("外部接口 → \"POST %s HTTP/1.1\" 发起 year=%s month=%s", url, year, month)
    try:
        resp = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as exc:
        logger.warning("briefing 上游拒绝 http=%s", exc.code)
        raise RuntimeError(f"简报服务返回错误（HTTP {exc.code}）") from exc
    except Exception as exc:
        logger.warning("briefing 上游不可达 err=%r", exc)
        raise RuntimeError("简报服务暂不可用，请稍后重试") from exc
    try:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip("\r\n")
            if line.startswith("data:"):
                payload = line[5:].lstrip(" ")
                if payload:
                    yield {"data": payload}
    except Exception as exc:
        logger.warning("briefing 上游流中断 err=%r", exc)
        raise RuntimeError("简报服务连接中断，请重试") from exc
    finally:
        resp.close()
