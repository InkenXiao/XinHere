"""组件卡处理器注册表（P2 改造后：平台层只留注册机制，不含任何业务实现）。

DSH 思想：平台核心不认识具体业务卡。kind → handler 的映射全部经 register()
注册进来——内置业务卡见 builtin_handlers（启动装配期注册），插件卡在
discover() 导入期由插件 backend 自行注册。同名 kind 不同实现 → fail-loud。
"""
from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _ai_copy(prompt: str, fallback: str, timeout_s: float = 8.0) -> str:
    """卡处理器内的一次性 LLM 文案（限时，失败回退确定性文案，不阻塞主流程）。"""
    from concurrent.futures import ThreadPoolExecutor

    from .llm import chat_once

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(chat_once, prompt).result(timeout=timeout_s).strip() or fallback
    except Exception:
        logger.warning("AI 文案生成失败/超时（回退确定性文案）")
        return fallback


# kind → submit 业务落库处理器（双写铁律：事件→业务→resume，由调用方编排）
HANDLERS: dict[str, Callable] = {}

# kind → 草稿 update → field-update 事件 payload 构造器（props, draft) -> (type, payload)
DRAFT_HANDLERS: dict[str, Callable[[dict, dict], tuple[str, dict]]] = {}


def _claim(key: str, table: dict, existed) -> bool:
    if existed is None:
        return True
    if existed is not table:
        raise RuntimeError(f"组件重复注册: {key}")
    return False  # 幂等：同函数重复注册（模块重执行）不报错


def register(kind: str, handler: Callable) -> None:
    """卡处理器注册扩展点（内置包与插件 backend 共用）。
    幂等：同函数重复注册（模块重执行）不报错；不同函数抢占同名 kind fail-loud。"""
    if _claim(kind, handler, HANDLERS.get(kind)):
        HANDLERS[kind] = handler


def register_draft_handler(kind: str, fn: Callable[[dict, dict], tuple[str, dict]]) -> None:
    if _claim(kind, fn, DRAFT_HANDLERS.get(kind)):
        DRAFT_HANDLERS[kind] = fn


def apply_update_draft(db: Session, *, kind: str, props: dict, draft: dict) -> tuple[str, dict] | None:
    """组件草稿 update → 对应 *-field-update 事件 payload；不落业务库为 None。"""
    fn = DRAFT_HANDLERS.get(kind)
    if fn is None:
        return None
    return fn(props, draft)
