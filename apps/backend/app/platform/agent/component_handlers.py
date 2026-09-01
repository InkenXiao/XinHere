from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy.orm import Session

from ...core import errors
from ...services import cash as cash_svc
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


# kind → submit 业务落库处理器（双写铁律：事件→业务→resume，由调用方编排）
HANDLERS: dict[str, Callable] = {
    "risk-dispatch-confirm": handle_risk_dispatch_confirm,
    "cash-guarantee-form": handle_cash_guarantee_form,
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
