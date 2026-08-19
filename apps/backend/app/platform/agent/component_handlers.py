from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy.orm import Session

from ...core import errors
from ...services import cash as cash_svc
from ...services import report as report_svc
from ...services import risk as risk_svc
from ..events.store import store

logger = logging.getLogger(__name__)


def _emit(session_id: str):
    return lambda type_, payload: store.append(session_id, type_, payload)


def handle_risk_dispatch_confirm(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    period = props["period"]
    companies = props["companies"]
    batch = risk_svc.create_batch(db, period=period, companies=companies, dispatcher_id=user_id)
    logger.info("风险填报下发 sid=%s batch=%s period=%s 企业数=%d",
                session_id, batch.batch_id, period, len(companies))
    summary = f"已下发 {len(companies)} 家被投企业风险填报任务（归属期：{period}）"
    events = [("risk/fill-start", {"batch_id": batch.batch_id, "period": period,
                                   "companies": companies, "version": 1})]
    return summary, events


def handle_cash_guarantee_form(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    if not values:
        raise errors.validation("缺少填报值")
    form_id = props["form_id"]
    cash_svc.save_draft(db, form_id, values)
    form = cash_svc.submit(db, form_id, dispatcher_id=user_id)
    lamp_txt = {"r": "红", "y": "黄", "g": "绿"}[form["lamp"]]
    logger.info("现金保障提交 sid=%s form=%s ratio=%s lamp=%s",
                session_id, form_id, form["ratio"], form["lamp"])
    summary = f"现金保障倍数 {form['ratio']}，亮{lamp_txt}灯"
    events = [("cash/form-submit", {
        "form_id": form_id,
        "values": {"avail_cash": form["avail_cash"], "pooled_fund": form["pooled_fund"],
                   "avail_credit": form["avail_credit"], "monthly_outflow": form["monthly_outflow"]},
        "ratio": form["ratio"], "lamp": form["lamp"], "summary": summary, "version": 1,
    })]
    return summary, events


def handle_report_confirm(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    """报告确认组件（report-confirm）：用户选定期间/模板/企业后落库。
    投后报告：create 报告行（幂等），异步生成由工具 resume 后启动（事件顺序 submit→report-start→update→file）；
    财务风险报告：产出 file/record 文件卡事件（PPT 内容由独立 skill 服务产出）。"""
    if not values:
        raise errors.validation("缺少确认值")
    period = str(values.get("period") or props.get("default_period") or "")
    if not period:
        raise errors.validation("请选择报告期间")
    skill = props.get("skill_key")
    template_name = next(
        (t["name"] for t in props.get("templates") or [] if t["key"] == values.get("template_key")),
        None,
    )
    if skill == "post_report":
        company_ids = values.get("company_ids") or []
        if not company_ids:
            raise errors.validation("请选择被投企业")
        r = report_svc.create(db, company_ids=company_ids, period=period,
                              report_id=props.get("report_id"))
        logger.info("投后报告确认落库 sid=%s report=%s period=%s 企业数=%d 模板=%s",
                    session_id, r.report_id, period, len(company_ids), template_name)
        summary = (f"投后管理报告生成中（归属期：{period}，{len(company_ids)} 家企业"
                   f"{f'，模板：{template_name}' if template_name else ''}），完成后将产出 Word 文档")
        return summary, []
    # fin_risk_report：仅时间+模板（设计稿无企业选择）
    name = f"财务风险报告-{period}.pptx"
    from urllib.parse import quote
    import uuid as _uuid

    events = [("file/record", {
        "file_id": _uuid.uuid4().hex, "name": name, "file_type": "pptx",
        "url": f"/demo.html?type=pptx&name={quote(name)}",
        "skill_key": "fin_risk_report", "version": 1,
    })]
    logger.info("财务风险报告确认落库 sid=%s period=%s 模板=%s", session_id, period, template_name)
    summary = (f"财务风险报告已生成（归属期：{period}"
               f"{f'，模板：{template_name}' if template_name else ''}），点击对话中的文件卡查看 PPT")
    return summary, events


# kind → submit 业务落库处理器（双写铁律：事件→业务→resume，由调用方编排）
HANDLERS: dict[str, Callable] = {
    "risk-dispatch-confirm": handle_risk_dispatch_confirm,
    "cash-guarantee-form": handle_cash_guarantee_form,
    "report-confirm": handle_report_confirm,
}


def apply_update_draft(db: Session, *, kind: str, props: dict, draft: dict) -> tuple[str, dict] | None:
    """组件草稿 update → 对应 *-field-update 事件 payload；不落业务库为 None。"""
    if kind == "cash-guarantee-form":
        for k in ("avail_cash", "pooled_fund", "avail_credit", "monthly_outflow"):
            if k not in draft:
                raise errors.validation(f"草稿缺字段 {k}")
        payload = {
            "form_id": props["form_id"],
            "draft": {k: float(draft[k]) for k in ("avail_cash", "pooled_fund", "avail_credit", "monthly_outflow")},
            "version": 1,
        }
        return "cash/form-field-update", payload
    return None
