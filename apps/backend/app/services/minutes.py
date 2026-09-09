"""实时会议纪要生成 · 阶段分析 / 完整纪要 / 润色 (流式)

Prompt 体系移植自 XuanPu pro-cowork realtime_minutes_service (内置默认, 无分身定制):
- stage: 五段式阶段分析 (每 60s 自动 / 停止时)
- final: 四段式完整会议纪要 (「生成纪要」/ 录音结束自动)
- polish: 高情商润色 (语气委婉 / 表达专业 / 篇幅控制)
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from ..platform.agent.llm import build_model

logger = logging.getLogger(__name__)

_PERSONA = "你是「实时会议纪要助手」，一名高级项目经理。你的职责是基于会议录音转写与人工补充的关键信息，实时产出结构清晰的会议纪要。"

# 阶段性分析格式 (60s 定时/停止时): 固定五段式 Markdown
_STAGE_FORMAT_SPEC = """一、会议主要讨论内容：
1. ...
2. ...
二、当前核心讨论点：...
三、问题：[提取文本中明确提到的痛点、分歧或阻碍。如果没有提到，请仅输出"无"，绝不凭空捏造]
四、方案：[提取文本中提出的建议或解决方案。如果没有，请仅输出"无"]
五、追问：[基于当前的讨论进度，提出 1 到 2 个高价值的追问，帮助与会者深化讨论、打破僵局或确认下一步行动]"""

# 完整会议纪要格式: 四段式
_FOUR_SECTION_SPEC = """输出结构固定为:
【会议主题】(根据内容概括; 可辨识时间/参会人也一并列出)
一、会议议程 (总结会议主要讨论/汇报的内容)
二、会议结论/当前进展 (逐条列出达成的共识与决策，或各自进展)
三、代办事项/后续任务 (以todolist逐条列出: 事项 - 负责人 - 截止时间; 未明确的标注"待定")
四、风险与遗留问题 (非必需; 有则逐条列出; 无则写"无")"""

# 润色 Prompt
_POLISH_SPEC = """你是一个专业且情商极高的会议纪要润色助手。请对用户提供的会议内容进行语法和语气的优化。
润色原则：
1. 语气委婉：问题描述不要太尖锐，结论不要太绝对（适当使用"建议"、"可能"、"倾向于"等词汇）。
2. 表达专业：确保语句通畅、层次逻辑清晰。
3. 篇幅控制：内容简明扼要，去除口语化和冗余表达，但绝不能丢失原有的核心信息点。"""

_RULES = """内容忠于转写原文, 禁止编造未提及的事实; 转写可能存在的同音错别字请按上下文修正;
语言简洁专业, 中文输出。"""


async def _stream(prompt: str) -> AsyncGenerator[str, None]:
    model = build_model()
    async for chunk in model.astream(prompt):
        content = chunk.content
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if content:
            yield str(content)


async def summarize_stream(
    transcript: str, mode: str = "final", instruction: str = ""
) -> AsyncGenerator[str, None]:
    """流式生成: stage 阶段分析 / final 最终纪要 / polish 润色, 逐段 yield 增量文本"""
    transcript = (transcript or "").strip()
    if mode != "polish" and not transcript:
        raise RuntimeError("转录文字为空，无法生成会议纪要")
    extra = f"\n用户补充要求：{(instruction or '').strip()}" if (instruction or "").strip() else ""

    if mode == "polish":
        prompt = (
            f"{_POLISH_SPEC}\n"
            f"请直接输出润色后的完整内容, 不要输出任何解释。{extra}\n"
            f"待润色的会议内容:\n{transcript[:30000]}"
        )
    elif mode == "final":
        prompt = (
            f"{_PERSONA}\n请根据以下会议录音转写文字整理最终会议纪要。\n\n"
            f"要求:\n1. {_FOUR_SECTION_SPEC}\n2. {_RULES}{extra}\n"
            f"会议录音转写文字:\n{transcript[:30000]}"
        )
    else:  # stage
        prompt = (
            f"{_PERSONA}\n会议正在进行中, 以下为截至目前已转写的录音文字。"
            f"请对当前讨论内容进行分析与总结。\n\n"
            f"要求:\n1. 必须严格按照以下 Markdown 格式输出, 不要输出任何格式之外的废话:\n"
            f"{_STAGE_FORMAT_SPEC}\n2. {_RULES}{extra}\n"
            f"会议录音转写文字:\n{transcript[:30000]}"
        )
    async for delta in _stream(prompt):
        yield delta
