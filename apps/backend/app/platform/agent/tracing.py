"""Langfuse LLM 观测（可选）：整轮 agent 链路（LLM 调用、工具执行、工具内嵌套生成）
上报 Langfuse，用于区分「程序问题」vs「模型访问问题」（token 用量、延迟、报错全程可见）。

启用条件：LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 三项齐备（core/config.py），
未配置时 trace_config 返回空 dict，graph.stream 调用与不接入时完全一致（零开销）。
观测自身异常一律 fail-soft：初始化失败记一次 warning 后本进程不再尝试，绝不影响对话主链路。
注意：知识库直连问答（kb_direct_qa，外部 SSE 直通）不经 graph，不在追踪范围内。"""

from __future__ import annotations

import logging
import threading

from ...core.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_client = None  # 进程级 Langfuse 单例（注册后 get_client() 按 key 复用凭证）
_init_failed = False


def trace_config(*, session_id: str, user_id: str, turn: int, model: str | None) -> dict:
    """返回挂进 graph.stream config 的追踪片段（callbacks + metadata）；未启用/失败返回 {}。

    metadata 约定（langfuse SDK 自动剥离 langfuse_* 前缀键上移到 trace 层）：
    - langfuse_session_id：Langfuse 会话 = 平台会话 id（同会话多轮在 UI 里按会话聚合）
    - langfuse_user_id：平台用户 id
    - langfuse_tags：环境 tag（dev/uat/prod）
    其余键（session_id/turn/model）进 trace metadata 供检索。"""
    global _init_failed
    if not settings.langfuse_enabled or _init_failed:
        return {}
    try:
        _ensure_client()
        from langfuse.langchain import CallbackHandler

        return {
            "callbacks": [CallbackHandler()],
            "metadata": {
                "langfuse_session_id": session_id,
                "langfuse_user_id": user_id,
                "langfuse_tags": [settings.app_env],
                "session_id": session_id,
                "turn": turn,
                "model": model or settings.main_model,
            },
        }
    except Exception as exc:
        _init_failed = True
        logger.warning("Langfuse 追踪初始化失败，本进程不再尝试（检查 LANGFUSE_* 配置/依赖）：%r", exc)
        return {}


def flush_tool_deliverables(*, name: str, arguments: dict, deliverables: dict | None) -> None:
    """把一次工具调用的真实交付物一次性写入 Langfuse（event 节点，挂在该工具节点下）。

    直通/出卡类工具的返回值只是给模型的提示文案，真实交付物（query_data 的答案正文、
    report/embed-card 等卡片 JSON）走流式/emit 通道，langchain CallbackHandler 捕获不到
    （on_tool_end 只取返回值占位符）。tool_scope 在该次工具调用期间统一收集卡片事件与
    直通答案正文（见 tool_base 的 _DeliverableCollector），工具调用收尾时由此一次性落库。
    工具执行期间 OTel 当前上下文仍是该工具 span（CallbackHandler 在 on_tool_start 用
    context.attach 挂上、on_tool_end 才 detach），event 即挂在该工具节点下；
    无活动 span 时 SDK 自行跳过，不生成孤儿 trace。
    未启用/无交付物/异常一律静默（fail-soft），绝不影响对话主链路。

    deliverables 结构：{"events": [{"type", "payload"}], "answer": 直通答案全文（可为空串）}"""
    if not settings.langfuse_enabled or _init_failed or not deliverables:
        return
    try:
        _ensure_client()
        # as_type="event"：零时长瞬时观测（start_observation 内部已 end，无需二次调用）
        _client.start_observation(
            name=f"{name} · 真实交付物",
            as_type="event",
            input=arguments or {},
            output={
                "answer": deliverables.get("answer") or None,
                "events": deliverables.get("events") or None,
            },
        )
    except Exception as exc:
        logger.debug("Langfuse 工具交付物落库失败（忽略）：%r", exc)


def _ensure_client() -> None:
    global _client
    if _client is not None:
        return
    with _lock:
        if _client is not None:
            return
        from langfuse import Langfuse

        _client = Langfuse(public_key=settings.langfuse_public_key,
                           secret_key=settings.langfuse_secret_key,
                           host=settings.langfuse_host)


def shutdown() -> None:
    """进程退出前 flush 批量导出缓冲区（未上报的尾部 trace）；未启用/失败静默。"""
    if _client is None:
        return
    try:
        _client.shutdown()
    except Exception:
        logger.warning("Langfuse shutdown flush 失败（可能丢最后一批 trace）", exc_info=True)
