from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated

from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import interrupt

from app.platform.agent.tool_base import ToolCtx, new_component_id, tool_scope
from app.services import report as report_svc
from app.services import skills as skills_svc
from app.services.common import COMPANIES

logger = logging.getLogger(__name__)


def _demo_file_url(file_type: str, name: str) -> str:
    """文件编辑页 URL（演示阶段落到内置 demo 页；后续由 skill 服务动态生成真实地址）。"""
    from urllib.parse import quote

    return f"/demo.html?type={file_type}&name={quote(name)}"


def _confirm_payload(
    *, skill_key: str, title: str, file_type: str, period: str | None,
    with_companies: bool, db,
) -> dict:
    """报告确认组件 payload：选项全部由后端填充（模型只选不填），draft 为模型从用户话语提取的预选。"""
    now = datetime.now()
    templates = [
        {"key": str(t["template_id"]), "name": t["name"]}
        for t in skills_svc.templates_for(db, skill_key)
        if t["enabled"]
    ]
    props: dict = {
        "skill_key": skill_key,
        "title": title,
        "file_type": file_type,
        "default_period": period or now.strftime("%Y-%m"),
        "year_options": [now.year - 1, now.year],
        "month_options": list(range(1, 13)),
        "templates": templates,
        # 设计稿：投后管理报告含企业多选；财务风险报告仅时间+模板
        "companies": list(COMPANIES) if with_companies else None,
    }
    if skill_key == "post_report":
        # report_id 由工具侧生成并随组件往返（values 带回），resume 后启动异步生成时幂等使用；
        # interrupt 前代码虽会重放，但该值仅在首次执行时随 request 事件落库，handler 按 props 使用
        props["report_id"] = uuid.uuid4().hex
    return {
        "component_id": new_component_id("report-confirm"),
        "kind": "report-confirm",
        "kind_version": 1,
        "props": props,
        "interrupt_id": new_component_id("intr"),
        "version": 1,
    }


def make_tools(ctx: ToolCtx) -> list:
    @tool(
        "generate_post_report",
        description="生成投后管理报告（产出 Word/.docx）。用户表达生成投后报告意图时立即调用；"
        "period（YYYY-MM）/company_ids（企业名称）可从用户话语中提取作预选，缺省由确认组件让用户选择。",
    )
    def generate_post_report(
        period: str | None = None,
        company_ids: list[str] | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"period": period, "company_ids": company_ids}
        with tool_scope(ctx, "generate_post_report", tool_call_id, args) as db:
            payload = _confirm_payload(
                skill_key="post_report", title="请确认以下信息", file_type="docx",
                period=period, with_companies=True, db=db,
            )
            if company_ids:
                payload["props"]["draft_company_ids"] = company_ids
            logger.info("投后报告确认组件挂起 sid=%s report_id=%s 预选=%s",
                        ctx.session_id, payload["props"]["report_id"], args)
            rv = interrupt(payload)  # resume 后从此返回；之前代码仅只读查询，重放安全
        if rv.get("action") == "cancelled":
            return "用户取消了本次投后管理报告生成"
        vals = rv.get("values") or {}
        report_id = str(vals.get("report_id") or payload["props"]["report_id"])
        sel_period = str(vals.get("period") or payload["props"]["default_period"])
        sel_companies = vals.get("company_ids") or []
        # 报告行已在 component/submit handler 落库（双写铁律）；resume 后发事件并启动异步生成
        ctx.emit_event(
            "pit/report-start",
            {"report_id": report_id, "company_ids": sel_companies, "period": sel_period,
             "outline": [], "version": 1},
        )

        def emit_update(idx: int, content: str) -> None:
            ctx.emit_event(
                "pit/report-update",
                {"report_id": report_id, "section_idx": idx, "content": content, "version": 1},
            )

        def emit_done() -> None:
            name = f"投后管理报告-{sel_period}.docx"
            ctx.emit_event(
                "file/record",
                {"file_id": uuid.uuid4().hex, "name": name, "file_type": "docx",
                 "url": _demo_file_url("docx", name), "skill_key": "post_report", "version": 1},
            )

        report_svc.generate_async(report_id, emit=emit_update, emit_done=emit_done)
        logger.info("投后报告异步生成启动 sid=%s report_id=%s period=%s 企业数=%d",
                    ctx.session_id, report_id, sel_period, len(sel_companies))
        return rv.get("summary") or f"投后报告生成中（report_id={report_id}），完成后将产出 Word 文档"

    @tool(
        "generate_fin_risk_report",
        description="生成财务风险报告（产出 PPT/.pptx）。用户表达生成财务风险报告意图时立即调用；"
        "period（YYYY-MM）可从用户话语中提取作预选，缺省由确认组件让用户选择。",
    )
    def generate_fin_risk_report(
        period: str | None = None,
        company_ids: list[str] | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"period": period, "company_ids": company_ids}
        with tool_scope(ctx, "generate_fin_risk_report", tool_call_id, args) as db:
            payload = _confirm_payload(
                skill_key="fin_risk_report", title="请确认报告时间", file_type="pptx",
                period=period, with_companies=False, db=db,
            )
            logger.info("财务风险报告确认组件挂起 sid=%s 预选=%s", ctx.session_id, args)
            rv = interrupt(payload)
        if rv.get("action") == "cancelled":
            return "用户取消了本次财务风险报告生成"
        return rv.get("summary") or "财务风险报告已生成，点击对话中的文件卡查看 PPT"

    return [generate_post_report, generate_fin_risk_report]
