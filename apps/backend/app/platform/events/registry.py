from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel

# packages/contracts 生成物：事件 payload 模型唯一来源
_CONTRACTS_PY = Path(__file__).resolve().parents[5] / "packages" / "contracts" / "generated" / "py"
if str(_CONTRACTS_PY) not in sys.path:
    sys.path.insert(0, str(_CONTRACTS_PY))

import events as ev  # noqa: E402

# type → (payload 模型, 默认 ignorable)
REGISTRY: dict[str, tuple[type[BaseModel], bool]] = {
    "turn/start": (ev.TurnStart, False),
    "turn/end": (ev.TurnEnd, False),
    "step/start": (ev.StepStart, False),
    "step/end": (ev.StepEnd, False),
    "user/message": (ev.UserMessage, False),
    "skill/start": (ev.SkillStart, False),
    "assistant/chunk": (ev.AssistantChunk, False),
    "assistant/message": (ev.AssistantMessage, False),
    "tool/call": (ev.ToolCall, False),
    "tool/result": (ev.ToolResult, False),
    "tool/progress": (ev.ToolProgress, True),
    "error": (ev.ErrorPayload, False),
    "component/request": (ev.ComponentRequest, False),
    "component/submit": (ev.ComponentSubmit, False),
    "feedback/record": (ev.FeedbackRecord, False),
    "file/record": (ev.FileRecord, False),
    "report/jump-card": (ev.ReportJumpCard, False),
    "report/embed-card": (ev.ReportEmbedCard, False),
    "report/session-bound": (ev.ReportSessionBound, False),
    "report/params-chosen": (ev.ReportParamsChosen, False),
    "chart/echarts-card": (ev.ChartEchartsCard, False),
    "html-report/card": (ev.HtmlReportCard, False),
    "indicator/audit-card": (ev.IndicatorAuditCard, False),
    "indicator/view-card": (ev.IndicatorViewCard, False),
    "indicator/stats-card": (ev.MilestoneStatsCard, False),
    # 业务事件族
    "risk/fill-start": (ev.RiskFillStart, False),
    "risk/report-update": (ev.RiskReportUpdate, False),
    "risk/submit": (ev.RiskSubmit, False),
    "risk/review": (ev.RiskReview, False),
    "cash/form-start": (ev.CashFormStart, False),
    "cash/form-field-update": (ev.CashFormFieldUpdate, False),
    "cash/form-submit": (ev.CashFormSubmit, False),
    "kpi/batch-start": (ev.KpiBatchStart, False),
    "kpi/indicator-update": (ev.KpiIndicatorUpdate, False),
    "kpi/ms-split": (ev.KpiMsSplit, False),
    "kpi/ms-feedback": (ev.KpiMsFeedback, False),
    "kpi/lamp-adjust": (ev.KpiLampAdjust, False),
    "kpi/review": (ev.KpiReview, False),
    "pit/report-start": (ev.PitReportStart, False),
    "pit/report-update": (ev.PitReportUpdate, False),
    "pit/report-done": (ev.PitReportDone, False),
    "todo/changed": (ev.TodoChanged, True),
}

# 不发 SSE 也不落库的伪帧类型由 bridge 处理，不入词表


def validate_payload(type_: str, data: dict) -> dict:
    """fail-closed：未知 type 且未标 ignorable → 拒绝。"""
    entry = REGISTRY.get(type_)
    if entry is None:
        raise ValueError(f"未知事件类型: {type_}")
    model, _ = entry
    return model.model_validate(data).model_dump(mode="json")


def default_ignorable(type_: str) -> bool:
    entry = REGISTRY.get(type_)
    return entry[1] if entry else False
