"""P7a 请求侧 tool-result 裁剪（确定性，无摘要模型参与）。

设计约束（对应 DSH surfaceOp:replace 的替代方案）：
- LangGraph checkpoint 是状态快照而非 append-only 事件日志，本模块**只做请求侧
  过滤**：返回全新 messages 列表，原消息对象一律只读复制（model_copy），checkpoint
  内的完整历史不受影响——不做 surface replace、不同步压缩、不做持久锁；
- **配对铁律**：绝不增删消息，只缩短陈旧 ToolMessage 的正文（content 为字符串时），
  tool call / result 配对天然不被破坏；
- 保留：全部 SystemMessage、首条 HumanMessage（任务约束）、最近 N 轮完整对话
  （按 HumanMessage 划轮）；其余（陈旧）ToolMessage 正文保留首尾 + 确定性裁剪标记
  （结构化摘要留待 P7b，在 P7a 指标观察后再决定）；
- token 预算：粗估（中文按 2 字符≈1 token）；裁剪陈旧正文后仍超预算时，从最旧
  开始对保留窗口内的 ToolMessage 正文同样收缩（最后一条 tool result 永不裁剪）。
"""
from __future__ import annotations

import logging

from langchain_core.messages import BaseMessage, ToolMessage

from ...core.config import settings
from . import otel

logger = logging.getLogger(__name__)

TRIM_MARK = "\n[…工具结果过长，已按上下文预算裁剪（原文 {n} 字，中间已省略，完整内容仍在会话记录中）…]\n"
_MIN_SAVE = 60  # 正文不足 head+tail+60 时裁剪无收益，跳过
_OVERHEAD_TOKENS = 8  # 每条消息的结构开销粗估


def _text_of(msg: BaseMessage) -> str:
    c = msg.content
    return c if isinstance(c, str) else ""


def _msg_tokens(msg: BaseMessage) -> int:
    return len(_text_of(msg)) // 2 + _OVERHEAD_TOKENS


def estimate_tokens(messages: list[BaseMessage]) -> int:
    return sum(_msg_tokens(m) for m in messages)


def _clip(text: str) -> str:
    head = max(settings.agent_trim_tool_head_chars, 0)
    tail = max(settings.agent_trim_tool_tail_chars, 0)
    return text[:head] + TRIM_MARK.format(n=len(text)) + (text[-tail:] if tail else "")


def _turn_starts(messages: list[BaseMessage]) -> list[int]:
    """每个 HumanMessage 的索引 = 一轮的开始。"""
    return [i for i, m in enumerate(messages) if m.type == "human"]


def _stale_tool_indices(messages: list[BaseMessage], window_start: int) -> list[int]:
    """保留窗口之前的 ToolMessage（str 正文）索引，按消息顺序（旧→新）。"""
    return [
        i for i, m in enumerate(messages[:window_start])
        if isinstance(m, ToolMessage) and isinstance(m.content, str) and m.content
    ]


def trim_for_request(messages: list[BaseMessage]) -> list[BaseMessage]:
    """按 token 预算裁剪陈旧 tool result 正文；checkpoint 不动、配对不破坏。"""
    if not settings.agent_trim_enabled or not messages:
        return list(messages)
    budget = max(settings.agent_trim_budget_tokens, 0)
    if estimate_tokens(messages) <= budget:
        return list(messages)

    humans = _turn_starts(messages)
    keep_first_human = humans[0] if humans else -1
    recent = max(settings.agent_trim_recent_turns, 0)
    # 最近 N 轮窗口起点：倒数第 N 个 HumanMessage；不足 N 轮时窗口覆盖到首条 human 之后
    window_start = humans[-recent] if len(humans) >= max(recent, 1) else (
        humans[0] if humans else len(messages))

    out: list[BaseMessage] = list(messages)
    replaced = 0
    before = estimate_tokens(out)

    def _shrink(indices: list[int]) -> None:
        nonlocal replaced
        for i in indices:
            m = out[i]
            body = _text_of(m)
            if len(body) <= max(settings.agent_trim_tool_head_chars, 0) \
                    + max(settings.agent_trim_tool_tail_chars, 0) + _MIN_SAVE:
                continue
            out[i] = m.model_copy(update={"content": _clip(body)})
            replaced += 1

    # 第一优先：保留窗口之外的陈旧 tool result（首条任务约束是 HumanMessage，不受影响）
    _shrink(_stale_tool_indices(out, window_start))
    # 第二优先：窗口内仍超预算时，从最旧开始收缩窗口内 ToolMessage（最后一条永不裁剪）
    if estimate_tokens(out) > budget:
        window_tools = [
            i for i, m in enumerate(out)
            if i >= window_start and isinstance(m, ToolMessage)
            and isinstance(m.content, str) and m.content
        ]
        _shrink(window_tools[:-1])

    if replaced == 0:
        return list(messages)
    saved = before - estimate_tokens(out)
    try:
        with otel.compaction_span() as obs:
            obs.set_attr("trimmed_messages", replaced)
            obs.set_attr("saved_tokens", max(saved, 0))
            obs.set_attr("budget_tokens", budget)
    except Exception:  # noqa: BLE001 - 观测失败不影响裁剪结果
        pass
    logger.info("P7a 请求侧裁剪：替换 %d 条陈旧 tool result，估算节省 %d tokens", replaced, max(saved, 0))
    return out
