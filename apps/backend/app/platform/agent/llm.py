from __future__ import annotations

from langchain_openai import ChatOpenAI

from ...core.config import settings


def build_model(channel: str = "main", **overrides) -> ChatOpenAI:
    """推理型模型：max_tokens 必须 >=4096；reasoning_content 不入历史（langchain 放 additional_kwargs）。

    channel: "main" 标准 / "small" 快速（未配置时回退标准）；其余值一律回退标准。
    """
    url, key, name = settings.main_api_url, settings.main_api_key, settings.main_model
    if channel == "small" and settings.small_model.strip():
        url = settings.small_api_url or settings.main_api_url
        key = settings.small_api_key or settings.main_api_key
        name = settings.small_model
    return ChatOpenAI(
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
