from __future__ import annotations

from langchain_openai import ChatOpenAI

from ...core.config import settings


def build_model(**overrides) -> ChatOpenAI:
    """推理型模型：max_tokens 必须 >=4096；reasoning_content 不入历史（langchain 放 additional_kwargs）。
    调用方可在 overrides 传 model 覆盖默认（前端模型选择的值对参数）。"""
    model = overrides.pop("model", None) or settings.main_model
    return ChatOpenAI(
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
