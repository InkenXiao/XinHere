from __future__ import annotations

from typing import Any, Iterator, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

from ...core.config import settings

import logging

logger = logging.getLogger(__name__)

_TOOL_UNSUPPORTED_MARK = "tool_choice"


class FallbackChatOpenAI(ChatOpenAI):
    """网关 vLLM 未开 tool parser（--enable-auto-tool-choice）时的容错：
    带 tools 请求被 400 拒绝 → 去掉 tools 降级为纯文本重试一次，保证普通对话可用。
    网关修复后工具调用自动恢复；降级期间工具类任务仅输出文字回答（日志有 warning）。"""

    @staticmethod
    def _tool_unsupported(e: Exception) -> bool:
        return _TOOL_UNSUPPORTED_MARK in str(e) and ("400" in str(e) or "tool" in str(e).lower())

    @staticmethod
    def _strip_tools(kwargs: dict) -> dict:
        kw = dict(kwargs)
        kw.pop("tools", None)
        kw.pop("tool_choice", None)
        return kw

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        try:
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as e:
            if not (kwargs.get("tools") and self._tool_unsupported(e)):
                raise
            logger.warning("网关不支持 tool calling，本次降级为纯文本回答（工具未执行）：%s", e)
            return super()._generate(messages, stop=stop, run_manager=run_manager,
                                     **self._strip_tools(kwargs))

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        try:
            yield from super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as e:
            if not (kwargs.get("tools") and self._tool_unsupported(e)):
                raise
            logger.warning("网关不支持 tool calling，本次降级为纯文本回答（工具未执行）：%s", e)
            yield from super()._stream(messages, stop=stop, run_manager=run_manager,
                                       **self._strip_tools(kwargs))


class _EmptyRetryChatOpenAI(FallbackChatOpenAI):
    """模型侧偶发「整条响应为空」（无文本、无工具调用）时自动重试的防御层。

    判定标准：message 无 content 且无 tool_calls/tool_call_chunks 即视为空。
    重试对上层透明：只有上一轮完全没有任何产出时才重发，已产出内容绝不重复。
    """

    empty_retries: int = 2

    @staticmethod
    def _has_payload(message: BaseMessage) -> bool:
        c = message.content
        if isinstance(c, str) and c.strip():
            return True
        if isinstance(c, list) and any(
            isinstance(p, dict) and (p.get("text") or p.get("type") == "tool_use") for p in c
        ):
            return True
        return bool(getattr(message, "tool_calls", None) or getattr(message, "tool_call_chunks", None))

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        last: ChatResult | None = None
        for _ in range(self.empty_retries + 1):
            last = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            if last.generations and self._has_payload(last.generations[0].message):
                return last
        return last

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        for _ in range(self.empty_retries + 1):
            emitted = False
            for chunk in super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
                emitted = emitted or self._has_payload(chunk.message)
                yield chunk
            if emitted:
                return


def build_model(channel: str = "main", *, user_id: str | None = None, **overrides) -> ChatOpenAI:
    """推理型模型：max_tokens 必须 >=4096；reasoning_content 不入历史（langchain 放 additional_kwargs）。

    channel: "main" 标准 / "small" 快速 / "coder" 代码生成（未配置时回退标准）；其余值一律回退标准。
    user_id: 提供时应用该用户的个人模型偏好覆盖（sys_user_model_prefs，空字段回退系统默认）。
    """
    ch = (channel or "main").strip().lower() or "main"
    url, key, name = settings.main_api_url, settings.main_api_key, settings.main_model
    mt = settings.main_max_tokens
    if ch == "small" and settings.small_model.strip():
        url = settings.small_api_url or settings.main_api_url
        key = settings.small_api_key or settings.main_api_key
        name = settings.small_model
        mt = settings.small_max_tokens or settings.main_max_tokens
    elif ch == "coder" and settings.coder_model.strip():
        url = settings.coder_api_url or settings.main_api_url
        key = settings.coder_api_key or settings.main_api_key
        name = settings.coder_model
        mt = settings.coder_max_tokens or settings.main_max_tokens
    if user_id:
        from ...services.model_pref import resolve_override

        pref = resolve_override(user_id, ch)
        if pref:
            url = pref.get("api_url") or url
            key = pref.get("api_key") or key
            name = pref.get("model") or name
            mt = int(pref.get("max_tokens") or 0) or mt
    return _EmptyRetryChatOpenAI(
        base_url=url,
        api_key=key,
        model=name,
        max_tokens=max(mt, 4096),
        timeout=120,
        **overrides,
    )


def chat_once(prompt: str, channel: str = "main", user_id: str | None = None) -> str:
    """后台一次性生成（报告小节等），同步调用——只在 worker 线程使用。"""
    msg = build_model(channel, user_id=user_id).invoke(prompt)
    content = msg.content
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content).strip()
