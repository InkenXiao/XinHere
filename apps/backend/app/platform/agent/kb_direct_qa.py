"""知识库直连问答（实验特性，独立文件，之后可能整体剥离）。

开关 KB_QA_ENABLED 打开且本轮选中了知识库范围（spaceIds/fileIds/folderIds 任一非空）时，
跳过 LLM agent，把「上下文 + 问题 + 范围 id」直接发给信投知识库网关的 CoWork 流式问答
（POST /api/v1/knowledge-import/cowork/answer），答案原封不动流式返回给前端
（复用 query_data 的直通正文通道：EventTranslator.begin/emit/end_tool_answer）。

鉴权复用 platform/kbimport/client：当前用户名 → cwk_ token（按用户缓存、401 强制重换）。
响应流为「一行一个 JSON」（非 data: 前缀 SSE）：meta → chunk* → done（失败 error），
正文只拼 choices[0].delta.content，不做任何加工。
KB_QA_URL 未配置或网关不可用时降级回原 LLM 链路（should_take_over 判空 / 异常上抛由调用方兜底）。
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from typing import Callable

from ...core.config import settings
from ..events.store import store
from ..kbimport import client as kbimport
from .tool_base import ToolAnswerStream

logger = logging.getLogger(__name__)

# query 构造参数（字符）：总长上限 + 历史问题单条上限（历史只带问题不带回答）
_QUERY_MAX_CHARS = 5000
_CTX_USER_MAX_CHARS = 200  # 单条历史问题截断上限
_SELFCONTAINED_MIN = 15  # 问题长度达到该值且不含指代词 → 视为独立问题，不做改写直发
# 指代/追问特征词（出现即认为问题依赖上下文，需要 LLM 改写）
_SELFCONTAINED_HINTS = "这|那|它|上述|刚才|刚刚|继续|还有|呢|另外|再问|再说说|重点|细节"
_DEIXIS_RE = re.compile(_SELFCONTAINED_HINTS)


def should_take_over(handle) -> bool:
    """是否走直连问答：开关开 + 网关已配置 + 本轮选中了知识库范围。"""
    if not settings.kb_qa_enabled:
        logger.info("知识库直连问答判定：开关关闭（KB_QA_ENABLED=false），走原 LLM 链路")
        return False
    if not kbimport.kb_import_enabled():
        logger.warning("KB_QA_ENABLED 已开但未配置 KB_IMPORT_*，知识库直连问答不生效（走原链路）")
        return False
    hit = bool(handle.space_ids or handle.file_ids or handle.folder_ids)
    logger.info("知识库直连问答判定 sid=%s 命中=%s spaceIds=%s fileIds=%s folderIds=%s",
                handle.session_id, hit, handle.space_ids, handle.file_ids, handle.folder_ids)
    return hit


def _extract_text(evt: dict) -> str:
    """从一行流事件取正文片段：只认 chunk.choices[0].delta.content，其余事件不产文本。"""
    if evt.get("event") != "chunk":
        return ""
    choices = evt.get("choices") or []
    if not choices:
        return ""
    return ((choices[0].get("delta") or {}).get("content")) or ""


def _recent_context(handle, limit: int) -> list[dict]:
    """从事件日志取本轮之前的最近对话（user/assistant），构造 [{role, content}]。

    user/message 落库不带 turn（历史遗留），用「剔除最后一条 user/message（即本轮问题）」
    的口径截取；limit 为 0/负 = 不带上下文。"""
    events, has_more = store.list_events(handle.session_id, limit=500)
    msgs: list[dict] = []
    for ev in events:
        if ev.type == "user/message":
            msgs.append({"role": "user", "content": (ev.data or {}).get("content") or ""})
        elif ev.type == "assistant/message":
            msgs.append({"role": "assistant", "content": (ev.data or {}).get("content") or ""})
    # 最后一条 user/message 是本轮刚落库的问题（chat 接口先 append 再 start_turn）
    if msgs and msgs[-1]["role"] == "user":
        msgs.pop()
    if limit <= 0:
        return []
    return msgs[-limit:]


def _history_questions(question: str, ctx: list[dict]) -> list[str]:
    """提取历史用户问题（去重保序、单条截断、剔除与当前问题相同的），新的在前。"""
    hist: list[str] = []
    seen = {question.strip()}
    for m in ctx:
        if m["role"] != "user":
            continue
        text = m["content"].strip()
        if not text or text in seen:
            continue
        seen.add(text)
        if len(text) > _CTX_USER_MAX_CHARS:
            text = text[:_CTX_USER_MAX_CHARS] + "…"
        hist.append(text)
    return list(reversed(hist))


def _looks_followup(question: str) -> bool:
    """判断是否为依赖上下文的追问/指代型问题：过短或含指代词。"""
    return len(question) < _SELFCONTAINED_MIN or bool(_DEIXIS_RE.search(question))


def rewrite_question(question: str, ctx: list[dict]) -> str:
    """基于历史问题用 LLM 把追问改写成独立问题（condense question），用于知识库检索。

    - 触发条件：问题像追问（过短/含指代词）且存在历史问题；否则直接返回原文（零开销）；
    - 检索纯度：改写后 query 是一句独立问题，与门户手工提问同形态，不被历史稀释；
    - 兜底：LLM 调用失败/超时/输出异常时，退回确定性拼接（当前问题 + 历史问题列表）。"""
    hist = _history_questions(question, ctx)
    if not hist or not _looks_followup(question):
        return question
    from langchain_core.messages import HumanMessage, SystemMessage
    from .llm import build_model
    sys = SystemMessage(content=(
        "你是问题改写器。根据历史问题，把用户的追问改写成一个不依赖任何上下文、"
        "可独立理解的完整问题（补全指代对象，保留追问意图与限定条件）。"
        "只输出改写后的问题本身，不要任何解释、引号或前缀；"
        "如果追问本身已经独立完整，原样输出它。"))
    user = HumanMessage(content=(
        "历史问题（按时间倒序）：\n"
        + "\n".join(f"{i}. {t}" for i, t in enumerate(hist, 1))
        + f"\n\n用户追问：{question}"))
    try:
        model = build_model()
        model.max_tokens = 512
        model.request_timeout = 15
        out = (model.invoke([sys, user]).content or "").strip()
        # 输出清理：剥可能的引号/换行，过短或过长视为异常走兜底
        out = out.strip('"“”‘’ \n')
        if 5 <= len(out) <= _QUERY_MAX_CHARS:
            logger.info("知识库直连问答问题改写 原文=%.60s → 改写=%.120s", question, out)
            return out
        return _fallback_query(question, hist)
    except Exception as exc:
        logger.warning("知识库直连问答问题改写失败，走拼接兜底 err=%r", exc)
        return _fallback_query(question, hist)


def _fallback_query(question: str, hist: list[str]) -> str:
    """确定性兜底：当前问题前置 + 历史问题列表后置（无 LLM 依赖）。"""
    nl = chr(10)
    head = ("请依据知识库检索结果回答<当前问题>。<历史问题>是同一会话中此前的提问，"
            "仅用于理解当前问题的背景与指代，不要作为回答依据。")
    lines = [f"{i}. {t}" for i, t in enumerate(hist, 1)]
    while lines and len(head) + len(question) + sum(len(l) + 1 for l in lines) > _QUERY_MAX_CHARS:
        lines.pop()  # 超总量：从最旧（列表尾）丢
    if not lines:
        return question
    return (head + f"{nl}{nl}<当前问题>{nl}{question}{nl}</当前问题>"
            + f"{nl}{nl}<历史问题>（按时间倒序）{nl}" + nl.join(lines) + nl + "</历史问题>")


def _answer_stream_once(biz_user: str, payload: dict, on_content: Callable[[str], None],
                        is_cancelled: Callable[[], bool]) -> tuple[str, list[dict], str | None]:
    """发一次 /cowork/answer（Bearer cwk_），逐行解析流事件。

    返回 (全文, citations, error)。on_content 实时回调正文片段（原封不动）。
    401 交由调用方清缓存重试。"""
    token = kbimport.token_for(biz_user)
    url = settings.kb_qa_url or f"{settings.kb_import_base.rstrip('/')}/api/v1/knowledge-import/cowork/answer"
    body = json.dumps(payload, ensure_ascii=False)
    logger.info("知识库直连问答 → \"POST %s HTTP/1.1\" 发起 user=%s\n  入参: %s", url, biz_user, body)
    t0 = time.time()
    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Service-Id": settings.kb_import_service_id,
            "Authorization": f"Bearer {token}",
        },
    )
    text_parts: list[str] = []
    citations: list[dict] = []
    error: str | None = None
    with urllib.request.urlopen(req, timeout=settings.kb_qa_timeout) as resp:
        for raw in resp:
            if is_cancelled():
                break
            line = raw.decode("utf-8").strip()
            # 兼容两种行格式：标准 SSE `data:{...}` 前缀与文档口径的裸 JSON 行
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line or line.startswith(":"):
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("知识库直连问答无法解析的行：%.120s", line)
                continue
            kind = evt.get("event")
            if kind == "chunk":
                piece = _extract_text(evt)
                if piece:
                    text_parts.append(piece)
                    on_content(piece)
            elif kind == "done":
                citations = evt.get("citations") or []
            elif kind == "error":
                error = str(evt.get("error") or "问答服务返回错误")
                logger.warning("知识库直连问答 error 事件：%.200s", evt)
    logger.info("知识库直连问答 ← \"POST %s HTTP/1.1\" 完成 耗时=%.1fs 正文=%d字 引用=%d条 错误=%s",
                url, time.time() - t0, sum(len(p) for p in text_parts), len(citations), error)
    return "".join(text_parts), citations, error


def run_direct_kb_qa(handle, question: str, translator) -> None:
    """直连问答主流程：拼 query（含上下文）→ 调 /cowork/answer → 答案原封不动流式直通。

    401 清 token 缓存重登重试一次；最终无正文时以一段错误提示作为答案收尾（保证轮次完整）。
    由 executor 在跳过 LLM 时调用（question=本轮用户原话），turn/end 等收尾由调用方负责。"""
    biz_user = kbimport.biz_username(handle.user_id, handle.username)
    ctx = _recent_context(handle, settings.kb_qa_context_messages)
    # 追问先由 LLM 改写成独立问题（独立问题直发零开销），保证检索纯度
    query = rewrite_question(question.strip(), ctx)
    payload: dict = {"query": query, "includePersonal": False}
    if handle.space_ids:
        payload["spaceIds"] = handle.space_ids
    if handle.folder_ids:
        payload["folderIds"] = handle.folder_ids
    if handle.file_ids:
        payload["fileIds"] = handle.file_ids

    stream = ToolAnswerStream(translator)

    for attempt in (0, 1):
        try:
            answer, citations, error = _answer_stream_once(
                biz_user, payload, stream.append, lambda: handle.cancel_event.is_set())
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                kbimport.invalidate_token(biz_user)
                logger.info("知识库直连问答 401（票据失效），重登重试 user=%s", biz_user)
                continue
            raise
    else:  # unreachable（循环必然 break 或 raise）
        raise RuntimeError("unreachable")

    if stream.streamed:
        stream.end()
        return
    # 无直通正文（接口失败/被取消/空答案）：给用户一段可见提示，保证轮次有交付
    tip = f"知识库问答未返回内容（{error or '请稍后重试'}）"
    stream.append(tip)
    stream.end()
