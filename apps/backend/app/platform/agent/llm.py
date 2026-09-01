from __future__ import annotations

from langchain_openai import ChatOpenAI

from ...core.config import settings


def build_model(**overrides) -> ChatOpenAI:
    """推理型模型：max_tokens 必须 >=4096；reasoning_content 不入历史（langchain 放 additional_kwargs）。"""
    return ChatOpenAI(
        base_url=settings.main_api_url,
        api_key=settings.main_api_key,
        model=settings.main_model,
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
