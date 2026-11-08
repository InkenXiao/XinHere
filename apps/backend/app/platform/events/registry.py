from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

# packages/contracts 生成物：事件 payload 模型唯一来源
_CONTRACTS_PY = Path(__file__).resolve().parents[5] / "packages" / "contracts" / "generated" / "py"
if str(_CONTRACTS_PY) not in sys.path:
    sys.path.insert(0, str(_CONTRACTS_PY))

import events as ev  # noqa: E402

# 平台框架新增事件的本地 payload 模型（尚未进 contracts 的过渡态）
class _SubagentStart(BaseModel):
    name: str
    provider: str
    depth: int = 1


class _SubagentEnd(BaseModel):
    name: str
    ok: bool
    output: str = ""
    error: str = ""
    duration_ms: int = 0


class _ApprovalGranted(BaseModel):
    """审批通过（P8a）：审批结果显式落会话日志，可回放。
    scope=审批对象（组件卡 kind）；tool=高影响工具名（工具级审批时填）。"""

    scope: str
    tool: str = ""
    turn: int = 0
    summary: str = ""


class _ApprovalDenied(BaseModel):
    """审批拒绝（P8a）：拒绝同样显式留痕。"""

    scope: str
    tool: str = ""
    turn: int = 0
    reason: str = ""


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
    # 子代理族（P4）：ignorable=True —— 前端装配器无感跳过，轨迹仍落库可追溯
    "subagent/start": (_SubagentStart, True),
    "subagent/end": (_SubagentEnd, True),
    # 审批族（P8a）：审批结果显式落日志（不只内存），可回放；前端暂无渲染器 → ignorable
    "approval/granted": (_ApprovalGranted, True),
    "approval/denied": (_ApprovalDenied, True),
}

# 不发 SSE 也不落库的伪帧类型由 bridge 处理，不入词表


class PluginEventPayload(BaseModel):
    """插件声明事件的宽松 payload 模型（extra=allow）。
    DSH 思想：SessionEventMap 由插件声明合并；payload schema 演进由插件自治，
    平台只维护词表唯一性与 fail-closed 校验（未知 type 拒绝写入）。"""

    model_config = ConfigDict(extra="allow")


def register_plugin_events(manifests: list[dict]) -> None:
    """插件 manifest.events → 注册进 REGISTRY（装载期调用一次）。
    与内置事件或跨插件重名 → fail-loud 拒绝启动；manifest 可用
    event_ignorable: [type...] 声明可忽略事件（默认 False）。"""
    for m in manifests:
        name = m.get("name", "?")
        ignorable_set = set(m.get("event_ignorable", []))
        for evt in m.get("events", []):
            if evt in REGISTRY:
                raise RuntimeError(f"事件类型重复注册: {evt} (插件 {name})")
            REGISTRY[evt] = (PluginEventPayload, evt in ignorable_set)


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
