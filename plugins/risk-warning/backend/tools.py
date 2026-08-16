from __future__ import annotations

import uuid
from typing import Annotated

from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import interrupt
from sqlalchemy import select

from app.persistence.models import CashGuaranteeReport
from app.platform.agent.tool_base import ToolCtx, new_component_id, tool_scope
from app.services import cash as cash_svc
from app.services import risk as risk_svc
from app.services.common import COMPANIES


def make_tools(ctx: ToolCtx) -> list:
    @tool(
        "dispatch_risk_fill",
        description="发起被投企业风险预警财务指标填报。period 为归属期（如 2026-07）。company_ids 可选限定公司（名称），缺省全部 11 家。",
    )
    def dispatch_risk_fill(
        period: str,
        company_ids: list[str] | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"period": period, "company_ids": company_ids}
        with tool_scope(ctx, "dispatch_risk_fill", tool_call_id, args):
            companies = company_ids if company_ids else list(COMPANIES)
            payload = {
                "component_id": new_component_id("risk-dispatch-confirm"),
                "kind": "risk-dispatch-confirm",
                "kind_version": 1,
                "props": {"period": period, "companies": companies},
                "interrupt_id": new_component_id("intr"),
                "version": 1,
            }
            rv = interrupt(payload)  # resume 后从此返回；之前代码会重放（仅只读查询）
        if rv.get("action") == "cancelled":
            return "用户取消了本次风险填报下发"
        return rv.get("summary", f"已下发风险填报任务（归属期：{period}）")

    @tool("get_risk_fill_status", description="查询风险填报进度（各状态计数）。batch_id 可选，缺省最新批次。")
    def get_risk_fill_status(
        batch_id: str | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "get_risk_fill_status", tool_call_id, {"batch_id": batch_id}) as db:
            c = risk_svc.status_counts(db, batch_id)
        if not c:
            return "暂无风险填报批次"
        total = c["unfilled"] + c["filled"] + c["reviewed"]
        return (
            f"批次 {c['batch_id']} 进度：未填 {c['unfilled']}，已填待审 {c['filled']}，"
            f"已审 {c['reviewed']}（共 {total} 家）"
        )

    @tool(
        "start_cash_guarantee_fill",
        description="为指定被投企业发起现金保障倍数试算填报（company 为企业名称，period 为归属期）。弹出试算表单由用户填写提交。",
    )
    def start_cash_guarantee_fill(
        company: str,
        period: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"company": company, "period": period}
        with tool_scope(ctx, "start_cash_guarantee_fill", tool_call_id, args) as db:
            sid = uuid.UUID(ctx.session_id) if ctx.session_id else None
            # 幂等：resume 重放时按本会话已建表单复用（不限状态——handler 可能已 submit），
            # 避免重放时原草稿已提交而重复建单
            form = db.scalars(
                select(CashGuaranteeReport).where(
                    CashGuaranteeReport.company == company,
                    CashGuaranteeReport.period == period,
                    CashGuaranteeReport.session_id == sid,
                ).order_by(CashGuaranteeReport.created_at.desc()).limit(1)
            ).first()
            if form is None:
                # 复用其它会话遗留草稿（预填 UX），并认领 session_id 保证重放幂等
                form = db.scalars(
                    select(CashGuaranteeReport).where(
                        CashGuaranteeReport.company == company,
                        CashGuaranteeReport.period == period,
                        CashGuaranteeReport.session_id.isnot(None),
                        CashGuaranteeReport.status == "draft",
                    ).order_by(CashGuaranteeReport.created_at.desc()).limit(1)
                ).first()
                if form is not None:
                    form.session_id = sid
            prev = None
            created = False
            if form is None:
                form, prev = cash_svc.create_form(
                    db, company=company, period=period, dispatcher_id=ctx.user_id,
                    session_id=ctx.session_id,
                )
                created = True
            fields = {
                "avail_cash": form.avail_cash,
                "pooled_fund": form.pooled_fund,
                "avail_credit": form.avail_credit,
                "monthly_outflow": form.monthly_outflow,
            }
            form_id = form.form_id
        if created:
            ctx.emit_event(
                "cash/form-start",
                {"form_id": form_id, "company": company, "period": period,
                 "fields": fields, "prev": prev, "version": 1},
            )
        payload = {
            "component_id": new_component_id("cash-guarantee-form"),
            "kind": "cash-guarantee-form",
            "kind_version": 1,
            "props": {"form_id": form_id, "company": company, "period": period,
                      "fields": fields, "prev": prev},
            "interrupt_id": new_component_id("intr"),
            "version": 1,
        }
        rv = interrupt(payload)
        if rv.get("action") == "cancelled":
            return "用户取消了本次现金保障倍数填报"
        return rv.get("summary", "现金保障倍数填报已提交")

    return [dispatch_risk_fill, get_risk_fill_status, start_cash_guarantee_fill]
