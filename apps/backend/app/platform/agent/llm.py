from __future__ import annotations

from typing import Any, Iterator, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

from ...core.config import settings


class _EmptyRetryChatOpenAI(ChatOpenAI):
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


def build_model(channel: str = "main", **overrides) -> ChatOpenAI:
    """推理型模型：max_tokens 必须 >=4096；reasoning_content 不入历史（langchain 放 additional_kwargs）。

    channel: "main" 标准 / "small" 快速（未配置时回退标准）；其余值一律回退标准。
    """
    url, key, name = settings.main_api_url, settings.main_api_key, settings.main_model
    if channel == "small" and settings.small_model.strip():
        url = settings.small_api_url or settings.main_api_url
        key = settings.small_api_key or settings.main_api_key
        name = settings.small_model
    return _EmptyRetryChatOpenAI(
        base_url=url,
        api_key=key,
        model=name,
        max_tokens=max(settings.llm_max_tokens, 4096),
        timeout=120,
        **overrides,
    )


def chat_once(prompt: str) -> str:
    """后台一次性生成（报告小节等），同步调用——只在 worker 线程使用。"""
    msg = build_model().invoke(prompt)
    content = msg.content
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content).strip()
