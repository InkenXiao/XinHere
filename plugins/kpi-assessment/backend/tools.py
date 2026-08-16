from __future__ import annotations

from typing import Annotated

from langchain_core.tools import InjectedToolCallId, tool

from app.platform.agent.tool_base import ToolCtx, tool_scope
from app.services import kpi as kpi_svc
from app.services.common import COMPANIES, list_companies


def make_tools(ctx: ToolCtx) -> list:
    @tool("dispatch_kpi_fill", description="发起经营者考核填报：创建批次并为各被投企业生成指标行与待办。period 为归属期。")
    def dispatch_kpi_fill(
        period: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "dispatch_kpi_fill", tool_call_id, {"period": period}) as db:
            companies = list_companies(db)
            batch = kpi_svc.create_batch(db, period=period, dispatcher_id=ctx.user_id)
            batch_id = batch.batch_id
        ctx.emit_event(
            "kpi/batch-start",
            {"batch_id": batch_id, "period": period, "companies": companies, "version": 1},
        )
        return f"已下发经营者考核填报 {len(companies)} 家（归属期：{period}）"

    @tool("dispatch_ms_feedback", description="对最新考核批次的里程碑发起反馈（被投企业填报进度与亮灯）。period 为归属期。")
    def dispatch_ms_feedback(
        period: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "dispatch_ms_feedback", tool_call_id, {"period": period}) as db:
            feedbacks = kpi_svc.dispatch_ms_feedback(db, period=period, dispatcher_id=ctx.user_id)
            snapshots = [
                {
                    "feedback_id": f.feedback_id,
                    "company": f.company,
                    "milestone_content": "",
                    "status": f.status,
                    "progress": f.progress,
                    "lamp": f.lamp,
                    "version": 1,
                }
                for f in feedbacks
            ]
        for snap in snapshots:
            ctx.emit_event("kpi/ms-feedback", snap)
        return f"已发起里程碑反馈 {len(feedbacks)} 项（归属期：{period}）"

    @tool("adjust_lamp", description="调整某被投企业某指标的亮灯（r/y/g），留痕可查。")
    def adjust_lamp(
        company: str,
        indicator_name: str,
        new_lamp: str,
        reason: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"company": company, "indicator_name": indicator_name, "new_lamp": new_lamp, "reason": reason}
        if new_lamp not in ("r", "y", "g"):
            return "非法灯色，仅支持 r/y/g"
        with tool_scope(ctx, "adjust_lamp", tool_call_id, args) as db:
            adj = kpi_svc.adjust_lamp(
                db, company=company, indicator_name=indicator_name,
                new_lamp=new_lamp, reason=reason, operator=ctx.user_id,
            )
            old_lamp = adj.old_lamp
        ctx.emit_event(
            "kpi/lamp-adjust",
            {"company": company, "indicator_name": indicator_name, "old_lamp": old_lamp,
             "new_lamp": new_lamp, "reason": reason, "version": 1},
        )
        return f"已将 {company}「{indicator_name}」由 {old_lamp} 灯调整为 {new_lamp} 灯并留痕"

    return [dispatch_kpi_fill, dispatch_ms_feedback, adjust_lamp]
