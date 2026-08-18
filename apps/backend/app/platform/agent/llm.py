from __future__ import annotations

import logging

from langchain_openai import ChatOpenAI

from ...core.config import settings

logger = logging.getLogger(__name__)

_TOOL_UNSUPPORTED_MARK = "enable-auto-tool-choice"


class FallbackChatOpenAI(ChatOpenAI):
    """网关 vLLM 未开 tool parser（--enable-auto-tool-choice）时的容错：
    带 tools 请求被 400 拒绝 → 去掉 tools 降级为纯文本重试一次，保证普通对话可用。
    网关修复后工具调用自动恢复；降级期间工具类任务仅输出文字回答（日志有 warning）。"""

    @staticmethod
    def _tool_unsupported(e: Exception) -> bool:
        return _TOOL_UNSUPPORTED_MARK in str(e)

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
            logger.warning("网关不支持 tool calling（vLLM 未开 --enable-auto-tool-choice），"
                           "本次降级为纯文本回答（工具未执行）：%s", e)
            return super()._generate(messages, stop=stop, run_manager=run_manager,
                                     **self._strip_tools(kwargs))

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        try:
            yield from super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as e:
            if not (kwargs.get("tools") and self._tool_unsupported(e)):
                raise
            logger.warning("网关不支持 tool calling（vLLM 未开 --enable-auto-tool-choice），"
                           "本次降级为纯文本回答（工具未执行）：%s", e)
            yield from super()._stream(messages, stop=stop, run_manager=run_manager,
                                       **self._strip_tools(kwargs))


def build_model(**overrides) -> ChatOpenAI:
    """推理型模型：max_tokens 必须 >=4096；reasoning_content 不入历史（langchain 放 additional_kwargs）。
    调用方可在 overrides 传 model 覆盖默认（前端模型选择的值对参数）。"""
    model = overrides.pop("model", None) or settings.main_model
    return FallbackChatOpenAI(
        base_url=settings.main_api_url,
        api_key=settings.main_api_key,
        model=model,
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
