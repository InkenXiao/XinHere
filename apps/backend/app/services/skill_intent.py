"""自动技能识别：用户未手选技能时，按请求内容判断是否应使用技能执行。

对齐 xuanpu intent_service 的对话入口分流模式：
- LLM（small 快速通道）判断用户请求与技能的匹配程度；
- 唯一高置信命中 → 自动加载执行（skill/start 事件带 auto 标记）；
- 有候选但不确定 → 返回候选列表，由前端弹层请用户确认；
- LLM 不可用/输出无效 → 关键词兜底（命中技能名给候选，不自动执行）；
- 全程失败 → 正常对话，不阻塞主流程。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from ..platform.agent.llm import build_model
from . import skill_files
from . import xuanpu as xuanpu_svc

logger = logging.getLogger(__name__)

# 市场技能列表识别缓存：避免每条消息都调 MCP 网关
_MARKET_TTL = 60.0
_market_cache: tuple[float, list[dict]] = (0.0, [])

_DETECT_PROMPT = """你是技能分流助手。判断用户请求是否应使用某个技能（预设的完整执行流程）完成。

## 可用技能
{skills}

## 判断规则
1. 仅当用户请求与某技能的适用场景明确匹配时，match 填该技能的 sel 值
2. 可能匹配但不确定（多个技能都可能或把握不足）时，match 留空，把 1~2 个可能的技能 sel 放入 candidates
3. 普通问答、数据查询、填报、闲聊等不适合走技能流程的请求，match 与 candidates 均留空
4. 只输出一行 JSON，不要输出其他内容：
{{"match": "sel或null", "candidates": ["sel"], "reason": "一句话理由"}}

## 用户请求
{input}"""


def _extract_json(text: str) -> dict | None:
    """从模型输出提取首个 JSON 对象（兼容代码围栏与前后噪声）。"""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    for cand in ([m.group(1)] if m else []) + [text]:
        start, end = cand.find("{"), cand.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(cand[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(data, dict):
                return data
    return None


def _local_candidates() -> list[dict]:
    return [
        {"sel": f"local:{sk['skill_key']}", "name": sk.get("name") or sk["skill_key"],
         "desc": (sk.get("desc") or "")[:80]}
        for sk in skill_files.list_file_skills()
    ]


def _market_candidates(identity: str) -> list[dict]:
    """市场技能列表（进程内 60s 缓存；平台不可达时仅用本地技能识别）。"""
    global _market_cache
    ts, cached = _market_cache
    now = time.monotonic()
    if now - ts < _MARKET_TTL:
        return cached
    try:
        items = xuanpu_svc.skills(identity) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("识别用市场技能列表获取失败（仅用本地技能识别）: %s", exc)
        items = []
    cands = [
        {"sel": f"xuanpu:{it.get('name', '')}", "name": it.get("name", ""),
         "desc": (it.get("description") or "")[:80]}
        for it in items if isinstance(it, dict) and it.get("name")
    ]
    _market_cache = (now, cands)
    return cands


def _invoke_llm(prompt: str) -> dict | None:
    """small 快速通道同步调用 + JSON 解析（在 worker 线程中执行）。"""
    try:
        msg = build_model("small").invoke(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("技能识别模型调用失败: %s", exc)
        return None
    content = msg.content
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return _extract_json(str(content))


def _keyword_fallback(text: str, cands: list[dict]) -> dict:
    """LLM 不可用兜底：请求文本命中技能名时给出候选（不自动执行）。"""
    hits = [c for c in cands if c["name"] and c["name"] in text]
    if hits:
        return {"mode": "candidates", "sel": "", "candidates": hits[:2]}
    return {"mode": "none", "sel": "", "candidates": []}


async def detect_skill(user, message: str, file_names: list[str] | None = None) -> dict:
    """识别本轮请求应使用的技能。

    返回 {"mode": "auto" | "candidates" | "none", "sel": str, "candidates": [...]}：
    - auto：唯一高置信命中，sel 为技能标识（local:<key> / xuanpu:<name>），可直接加载；
    - candidates：不确定，candidates 为候选列表（[{sel, name, desc}]），交用户确认；
    - none：无匹配，正常对话。
    """
    identity = user.display_name or user.username
    cands = _local_candidates() + _market_candidates(identity)
    if not cands:
        return {"mode": "none", "sel": "", "candidates": []}
    sel_map = {c["sel"]: c for c in cands}

    text = (message or "").strip()
    if file_names:
        text = f"{text}\n（附件：{'、'.join(file_names)}）"
    prompt = _DETECT_PROMPT.format(
        skills="\n".join(f"- sel={c['sel']} 名称={c['name']}：{c['desc']}" for c in cands) or "（无）",
        input=text[:4000] or "（空）",
    )
    data: dict | None = None
    try:
        # 外层超时兜底：识别失败不阻塞对话主流程
        data = await asyncio.wait_for(asyncio.to_thread(_invoke_llm, prompt), timeout=25.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("技能自动识别超时/异常，走关键词兜底: %s", exc)

    if isinstance(data, dict):
        match = str(data.get("match") or "").strip()
        if match in sel_map:
            logger.info("技能自动识别命中 sel=%s reason=%s", match, str(data.get("reason") or "")[:100])
            return {"mode": "auto", "sel": match, "candidates": []}
        picked: list[dict] = []
        raw = data.get("candidates")
        if isinstance(raw, list):
            for s in raw:
                c = sel_map.get(str(s).strip())
                if c and all(c["sel"] != p["sel"] for p in picked):
                    picked.append(c)
        if picked:
            logger.info("技能自动识别给出候选 %s", [p["sel"] for p in picked])
            return {"mode": "candidates", "sel": "", "candidates": picked[:2]}
        # LLM 明确判断无需技能 → 不再兜底，避免普通问答被误匹配
        return {"mode": "none", "sel": "", "candidates": []}
    return _keyword_fallback(text, cands)
