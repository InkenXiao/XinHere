from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from langchain_core.tools import InjectedToolCallId, tool

from app.platform.agent.tool_base import ToolCtx, tool_scope
from app.services import report as report_svc


def _demo_file_url(file_type: str, name: str) -> str:
    """文件编辑页 URL（演示阶段落到内置 demo 页；后续由 skill 服务动态生成真实地址）。"""
    return f"/demo.html?type={file_type}&name={quote(name)}"


def make_tools(ctx: ToolCtx) -> list:
    @tool(
        "generate_post_report",
        description="生成投后管理报告（异步），产出 Word 文档（.docx）。company_ids 为企业名称列表，period 为归属期。",
    )
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

        def emit_done() -> None:
            name = f"投后管理报告-{period}.docx"
            ctx.emit_event(
                "file/record",
                {"file_id": uuid.uuid4().hex, "name": name, "file_type": "docx",
                 "url": _demo_file_url("docx", name), "skill_key": "post_report", "version": 1},
            )

        ctx.emit_event(
            "pit/report-start",
            {"report_id": report_id, "company_ids": company_ids, "period": period,
             "outline": [], "version": 1},
        )
        report_svc.generate_async(report_id, emit=emit_update, emit_done=emit_done)
        return f"投后报告生成中（report_id={report_id}），覆盖 {len(company_ids)} 家企业，完成后将产出 Word 文档"

    @tool(
        "generate_fin_risk_report",
        description="生成财务风险报告，产出 PPT 演示文稿（.pptx）。company_ids 为企业名称列表，period 为归属期（如 2026-07）。",
    )
    def generate_fin_risk_report(
        company_ids: list[str],
        period: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        # 文件内容由独立 skill 服务产出（用户已建）；此处落文件记录事件供对话窗展示
        name = f"财务风险报告-{period}.pptx"
        ctx.emit_event(
            "file/record",
            {"file_id": uuid.uuid4().hex, "name": name, "file_type": "pptx",
             "url": _demo_file_url("pptx", name), "skill_key": "fin_risk_report", "version": 1},
        )
        return f"财务风险报告已生成（{period}，{len(company_ids)} 家企业），点击对话中的文件卡查看 PPT"

    return [generate_post_report, generate_fin_risk_report]
