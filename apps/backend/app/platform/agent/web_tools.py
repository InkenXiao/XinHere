"""联网搜索工具（技能轮次装配）：web_search 检索公开资料、web_fetch 抓网页正文。

出站走 SCMS 网关 MCP 工具端点（基址与问数/简报同源 settings.scms_ai_backend，
完整路径代码拼接）：
- POST {base}/mcp/tools/web_search  body {query, max_results, time_range}
- POST {base}/mcp/tools/web_fetch   body {url, max_chars}
鉴权同问数链路：优先透传发起用户 token（tool_scope 已绑定 ctx.scms_token），
401（token 失效/越权）回落服务账号重试一次。
装配时机：仅技能轮次（executor._build_graph，skill_key 非空时）——普通轮联网走
query_data 的 enable_web_search 透传问数上游，两条链路互不影响。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Annotated

from langchain_core.tools import InjectedToolCallId, tool

from ...core.config import settings
from .tool_base import ToolAnswerStream, ToolCtx, tool_scope

logger = logging.getLogger(__name__)

# 出站超时与参数上限（技能轮上下文预算 64K：摘要与正文都截短返回，防一次检索吃满预算）
_SEARCH_TIMEOUT = 20
_FETCH_TIMEOUT = 30
_RESULTS_CAP = 5  # max_results 硬顶（业务约束：联网结果控制在 5 条）
_SNIPPET_CAP = 200  # 单条摘要截断（对齐 query_data 溯源 web_results 的 200 字）
_FETCH_CHARS_CAP = 20000  # max_chars 硬顶（防止单页正文占掉大半上下文）
# 每轮 web_search 次数硬配额（用户业务约束：技能执行最多联网搜索 2 次）
_SEARCH_QUOTA = 2


def _mcp_tool_url(path: str) -> str:
    """网关 MCP 工具端点完整 URL（已实测：前缀 /ai-api/mcp/tools/，与 nl2sql 同网关同鉴权）。"""
    return settings.scms_ai_backend.rstrip("/") + f"/ai-api/mcp/tools/{path}"


def _gateway_post(tag: str, path: str, body: dict, timeout: int) -> dict:
    """POST 网关 MCP 工具端点，返回响应的 result 对象；凭证策略同问数（用户 token 401 回落服务账号）。"""
    from .tools import _scms_call_with_fallback, _token_source

    url = _mcp_tool_url(path)

    def _call(token: str) -> dict:
        # 外部接口审计日志（对齐问数流风格）：URL/入参/凭证一目了然
        logger.info(
            "外部接口 → \"POST %s HTTP/1.1\" 发起 token=%s\n  入参: %s",
            url, _token_source(), json.dumps(body, ensure_ascii=False),
        )
        t0 = time.time()
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-SCMS-Access-Token": token},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out = json.loads(resp.read().decode("utf-8"))
            logger.info(
                "外部接口 ← \"POST %s HTTP/1.1\" %d OK 耗时=%.1fs\n  返回: %s",
                url, resp.status, time.time() - t0,
                json.dumps(out, ensure_ascii=False)[:2000],
            )
            if not isinstance(out, dict):
                return {}
            return out.get("result") or {}
        except urllib.error.HTTPError as exc:
            logger.warning(
                "外部接口 ← \"POST %s HTTP/1.1\" %d 失败 耗时=%.1fs token=%s",
                url, exc.code, time.time() - t0, _token_source(),
            )
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning(
                "外部接口 ← \"POST %s HTTP/1.1\" 连接失败 耗时=%.1fs err=%r",
                url, time.time() - t0, exc,
            )
            raise

    return _scms_call_with_fallback(tag, _call)


def _format_search(result: dict) -> str:
    """搜索结果 → 紧凑编号列表（标题/链接/摘要），供模型挑线索后用 web_fetch 深读。
    网关可能无视 max_results 超量返回，统一截前 _RESULTS_CAP 条。"""
    results = [r for r in (result.get("results") or []) if isinstance(r, dict)][:_RESULTS_CAP]
    if not results:
        return "未检索到相关资料，请更换关键词或放宽时间范围后重试。"
    provider = str(result.get("provider") or "").strip()
    head = f"检索到 {len(results)} 条结果" + (f"（搜索源：{provider}）" if provider else "") + "："
    lines = [head]
    for i, r in enumerate(results, 1):
        title = str(r.get("title") or "").strip() or "（无标题）"
        lines.append(f"{i}. {title}")
        url = str(r.get("url") or "").strip()
        if url:
            lines.append(f"   链接：{url}")
        snippet = str(r.get("snippet") or "").strip()
        if snippet:
            lines.append(f"   摘要：{snippet[:_SNIPPET_CAP]}")
    lines.append("需要某条结果的网页正文时，用 web_fetch 传入对应链接抓取。")
    return "\n".join(lines)


def build_web_tools(ctx: ToolCtx) -> list:
    """技能轮装配 web_search / web_fetch（读-only 出站，失败降级为文案不中断会话）。

    每轮硬性配额（deep_research 等技能实测模型会连发 8+ 次换关键词重搜，网关为
    关键词匹配质量有限，重搜既浪费 10s/次又刷屏）：web_search 最多 _SEARCH_QUOTA 次，
    超限硬性拒绝并指示模型停止重搜；来源卡围栏每轮只出一张（多张刷屏）。"""
    quota = {"search": 0, "fence": False}
    quota_lock = threading.Lock()

    @tool(
        "web_search",
        description=(
            "联网搜索公开互联网资料（技能执行轮可用）：检索行业动态、政策法规、市场行情、"
            "企业公开信息等外部资料，返回标题/链接/摘要列表。query 用简洁关键词；"
            "max_results=返回条数（默认 5，上限 5）；time_range=时间限定"
            "（oneDay/oneWeek/oneMonth/oneYear，空=不限）。"
            "【硬性限额】每轮对话最多调用 2 次，超限会被直接拒绝——检索结果不理想时"
            "不要反复换关键词重搜，改为用 web_fetch 直接抓取已找到链接或已知权威来源的正文。"
            "仅用于公开网络资料；企业内部业务数据一律用 run_skill_script 获取，"
            "不得拿联网内容冒充内部数据。需要某条结果的正文时再用 web_fetch 抓取。"
        ),
    )
    def web_search(
        query: str,
        max_results: int = 5,
        time_range: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with quota_lock:
            if quota["search"] >= _SEARCH_QUOTA:
                return (
                    f"[限额] 本轮联网搜索已用完（上限 {_SEARCH_QUOTA} 次），禁止再调用 web_search。"
                    "请基于已获取的检索结果与网页正文继续分析并输出结论；"
                    "确需某页详细内容时用 web_fetch 抓取已有链接，不要换关键词重搜。"
                )
            quota["search"] += 1
        q = (query or "").strip()
        if not q:
            return "搜索词不能为空，请给出要检索的关键词。"
        try:
            n = int(max_results)
        except (TypeError, ValueError):
            n = 5
        n = max(1, min(n, _RESULTS_CAP))
        tr = (time_range or "").strip()
        args = {"query": q, "max_results": n, "time_range": tr}
        with tool_scope(ctx, "web_search", tool_call_id, args):
            try:
                result = _gateway_post("联网搜索", "web_search", args, _SEARCH_TIMEOUT)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                logger.warning("联网搜索不可达 sid=%s err=%r", ctx.session_id, exc)
                return f"联网搜索服务暂不可用，请稍后再试（{exc}）"
            except Exception as exc:
                logger.warning("联网搜索异常 sid=%s err=%r", ctx.session_id, exc)
                return f"联网搜索失败：{exc}"
            if not result.get("success"):
                return f"搜索失败：{result.get('error') or '服务端未返回原因'}"
            # 前端展示：结果经直通流以 ```sources 围栏出结构化来源卡（SourceList，
            # 与 query_data 溯源同一渲染链路，前端零适配）；技能轮为 defer 模式，
            # 后续模型最终回答不受直通压制。仅工具文本仍完整给模型做分析。
            hits = [r for r in (result.get("results") or [])
                    if isinstance(r, dict) and r.get("url")][:_RESULTS_CAP]
            with quota_lock:
                emit_fence = hits and ctx.answer_stream is not None and not quota["fence"]
                if emit_fence:
                    quota["fence"] = True
            if emit_fence:
                payload = {"web_results": [{
                    "title": str(r.get("title") or r.get("url")),
                    "url": str(r.get("url")),
                    "snippet": str(r.get("snippet") or "")[:_SNIPPET_CAP],
                } for r in hits], "route": "web_search"}
                stream = ToolAnswerStream(ctx.answer_stream, "web_search")
                stream.append("\n```sources\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n")
                stream.end()
        return _format_search(result)

    @tool(
        "web_fetch",
        description=(
            "抓取网页正文纯文本（技能执行轮可用）：通常配合 web_search——传入搜索结果里的链接，"
            "返回页面标题与正文纯文本（已剥离脚本/样式、压缩空白）。"
            "max_chars=正文字符上限（默认 4096）。优先只抓与问题最相关的少数页面，节约上下文。"
        ),
    )
    def web_fetch(
        url: str,
        max_chars: int = 4096,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        u = (url or "").strip()
        if not u.lower().startswith(("http://", "https://")):
            return "url 非法（须以 http(s):// 开头），请传 web_search 结果中的链接。"
        try:
            chars = int(max_chars)
        except (TypeError, ValueError):
            chars = 4096
        chars = max(256, min(chars, _FETCH_CHARS_CAP))
        args = {"url": u, "max_chars": chars}
        with tool_scope(ctx, "web_fetch", tool_call_id, args):
            try:
                result = _gateway_post("网页抓取", "web_fetch", args, _FETCH_TIMEOUT)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                logger.warning("网页抓取不可达 sid=%s err=%r", ctx.session_id, exc)
                return f"网页抓取服务暂不可用，请稍后再试（{exc}）"
            except Exception as exc:
                logger.warning("网页抓取异常 sid=%s err=%r", ctx.session_id, exc)
                return f"网页抓取失败：{exc}"
        if not result.get("success"):
            return f"抓取失败：{result.get('error') or '服务端未返回原因'}"
        title = str(result.get("title") or "").strip()
        content = str(result.get("content") or "").strip()
        if not content:
            return f"「{title or u}」未取到正文（页面可能需登录或为动态渲染），建议换其他来源。"
        head = (f"《{title}》\n{result.get('url') or u}\n\n") if title else ""
        return f"{head}{content[:chars]}"

    return [web_search, web_fetch]
