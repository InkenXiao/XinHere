from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import errors
from ..persistence.models import CashGuaranteeReport
from . import todo as todo_svc


def compute(values: dict) -> tuple[float, str]:
    """ratio=(可用货币资金+资金集中+可用授信)/月均支出；<=3 红，<=6 黄，否则绿。"""
    outflow = float(values.get("monthly_outflow") or 0)
    if outflow <= 0:
        return 0.0, "r"
    ratio = (
        float(values.get("avail_cash") or 0)
        + float(values.get("pooled_fund") or 0)
        + float(values.get("avail_credit") or 0)
    ) / outflow
    lamp = "r" if ratio <= 3 else ("y" if ratio <= 6 else "g")
    return round(ratio, 2), lamp


def view(r: CashGuaranteeReport) -> dict:
    return {
        "form_id": r.form_id,
        "company": r.company,
        "period": r.period,
        "avail_cash": r.avail_cash,
        "pooled_fund": r.pooled_fund,
        "avail_credit": r.avail_credit,
        "monthly_outflow": r.monthly_outflow,
        "ratio": r.ratio,
        "lamp": r.lamp,
        "status": r.status,
    }


def _values_of(r: CashGuaranteeReport) -> dict:
    return {
        "avail_cash": r.avail_cash,
        "pooled_fund": r.pooled_fund,
        "avail_credit": r.avail_credit,
        "monthly_outflow": r.monthly_outflow,
    }


def create_form(
    db: Session, *, company: str, period: str, dispatcher_id: str, session_id: str | None = None
) -> tuple[CashGuaranteeReport, dict | None]:
    """建单并带上月预填值；返回 (form, prev_values)。"""
    prev_row = db.scalars(
        select(CashGuaranteeReport)
        .where(CashGuaranteeReport.company == company)
        .order_by(CashGuaranteeReport.created_at.desc())
        .limit(1)
    ).first()
    form = CashGuaranteeReport(company=company, period=period, session_id=session_id)
    if prev_row is not None:
        form.avail_cash = prev_row.avail_cash
        form.pooled_fund = prev_row.pooled_fund
        form.avail_credit = prev_row.avail_credit
        form.monthly_outflow = prev_row.monthly_outflow
        prev = _values_of(prev_row)
    else:
        prev = None
    form.ratio, form.lamp = compute(_values_of(form))
    db.add(form)
    db.flush()
    return form, prev


def list_forms(db: Session, company: str | None = None, period: str | None = None) -> list[dict]:
    stmt = select(CashGuaranteeReport).order_by(CashGuaranteeReport.created_at.desc())
    if company:
        stmt = stmt.where(CashGuaranteeReport.company == company)
    if period:
        stmt = stmt.where(CashGuaranteeReport.period == period)
    return [view(r) for r in db.scalars(stmt).all()]


def get(db: Session, form_id: str) -> CashGuaranteeReport:
    r = db.get(CashGuaranteeReport, form_id)
    if r is None:
        raise errors.not_found("现金保障单不存在")
    return r


def save_draft(db: Session, form_id: str, values: dict) -> dict:
    r = get(db, form_id)
    if r.status == "reviewed":
        raise errors.validation("已审批，不可修改")
    for k in ("avail_cash", "pooled_fund", "avail_credit", "monthly_outflow"):
        if k in values and values[k] is not None:
            setattr(r, k, float(values[k]))
    r.ratio, r.lamp = compute(_values_of(r))
    db.flush()
    return view(r)


def submit(db: Session, form_id: str, dispatcher_id: str) -> dict:
    r = get(db, form_id)
    if r.status not in ("draft",):
        raise errors.validation(f"当前状态 {r.status} 不可提交")
    r.status = "submitted"
    task = todo_svc.create_task(
        db, scene="cash_guarantee", title=f"现金保障倍数审批（{r.company}）",
        dispatcher_id=dispatcher_id, payload={"form_id": form_id}, period=r.period,
    )
    todo_svc.create_todo(
        db, task=task, assignee_id=dispatcher_id, kind="review",
        title=f"{r.company} · 现金保障倍数审批",
        sub=f"归属期：{r.period} · 倍数 {r.ratio}",
        lamp=r.lamp,
        ref={"form_id": form_id, "company": r.company},
    )
    db.flush()
    return view(r)


def review(db: Session, form_id: str, approve: bool, comment: str | None) -> dict:
    r = get(db, form_id)
    if r.status != "submitted":
        raise errors.validation("仅已提交状态可审批")
    r.status = "reviewed" if approve else "draft"
    db.flush()
    return view(r)
