from __future__ import annotations

from typing import Annotated

from langchain_core.tools import InjectedToolCallId, tool

from app.platform.agent.tool_base import ToolCtx, tool_scope
from app.services import report as report_svc


def make_tools(ctx: ToolCtx) -> list:
    @tool("generate_post_report", description="生成投后管理报告（异步）。company_ids 为企业名称列表，period 为归属期。")
    def generate_post_report(
        company_ids: list[str],
        period: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"company_ids": company_ids, "period": period}
        with tool_scope(ctx, "generate_post_report", tool_call_id, args) as db:
            r = report_svc.create(db, company_ids=company_ids, period=period)
            report_id = r.report_id

        def emit_update(idx: int, content: str) -> None:
            ctx.emit_event(
                "pit/report-update",
                {"report_id": report_id, "section_idx": idx, "content": content, "version": 1},
            )

        ctx.emit_event(
            "pit/report-start",
            {"report_id": report_id, "company_ids": company_ids, "period": period,
             "outline": [], "version": 1},
        )
        report_svc.generate_async(report_id, emit=emit_update)
        return f"投后报告生成中（report_id={report_id}），覆盖 {len(company_ids)} 家企业"

    return [generate_post_report]
