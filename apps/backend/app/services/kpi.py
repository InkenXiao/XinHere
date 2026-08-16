from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import errors
from ..persistence.models import (
    KpiBatch,
    KpiIndicator,
    KpiLampAdjustment,
    KpiMilestone,
    KpiMsFeedback,
)
from . import todo as todo_svc
from .common import company_user, list_companies

# KPI 指标模板 4 行
KPI_TEMPLATE = [
    {"dim": "财务", "name": "营业收入", "kpi_type": "定量", "base_score": "0", "max_score": "30"},
    {"dim": "财务", "name": "净利润", "kpi_type": "定量", "base_score": "0", "max_score": "30"},
    {"dim": "转型", "name": "数字化转型进度", "kpi_type": "定性", "base_score": "0", "max_score": "20"},
    {"dim": "合规", "name": "合规经营", "kpi_type": "定性", "base_score": "0", "max_score": "20"},
]


def indicator_view(i: KpiIndicator) -> dict:
    return {
        "indicator_id": i.indicator_id,
        "batch_id": i.batch_id,
        "company": i.company,
        "dim": i.dim,
        "name": i.name,
        "kpi_type": i.kpi_type,
        "content": i.content,
        "base_score": i.base_score,
        "max_score": i.max_score,
        "status": i.status,
    }


def milestone_view(m: KpiMilestone) -> dict:
    return {
        "milestone_id": m.milestone_id,
        "indicator_id": m.indicator_id,
        "content": m.content,
        "plan_date": m.plan_date,
        "material": m.material,
        "status": m.status,
    }


def feedback_view(f: KpiMsFeedback) -> dict:
    return {
        "feedback_id": f.feedback_id,
        "milestone_id": f.milestone_id,
        "batch_id": f.batch_id,
        "company": f.company,
        "status": f.status,
        "progress": f.progress,
        "actual_date": f.actual_date,
        "lamp": f.lamp,
        "status_note": f.status_note,
        "review_status": f.review_status,
    }


def create_batch(db: Session, *, period: str, dispatcher_id: str) -> KpiBatch:
    batch = KpiBatch(period=period, dispatcher_id=dispatcher_id)
    db.add(batch)
    db.flush()
    task = todo_svc.create_task(
        db, scene="kpi_fill", title=f"经营者考核填报（{period}）",
        dispatcher_id=dispatcher_id, payload={"batch_id": batch.batch_id}, period=period,
    )
    for company in list_companies(db):
        for tpl in KPI_TEMPLATE:
            db.add(KpiIndicator(batch_id=batch.batch_id, company=company, **tpl))
        user = company_user(db, company)
        if user:
            todo_svc.create_todo(
                db, task=task, assignee_id=user.user_id, kind="action",
                title=f"{company} · 经营者考核填报", sub=f"归属期：{period}",
                ref={"batch_id": batch.batch_id, "company": company},
            )
    db.flush()
    return batch


def get_batch(db: Session, batch_id: str) -> KpiBatch:
    b = db.get(KpiBatch, batch_id)
    if b is None:
        raise errors.not_found("考核批次不存在")
    return b


def company_rows(db: Session, batch_id: str, company: str) -> dict:
    get_batch(db, batch_id)
    indicators = db.scalars(
        select(KpiIndicator).where(KpiIndicator.batch_id == batch_id, KpiIndicator.company == company)
    ).all()
    ids = [i.indicator_id for i in indicators]
    milestones = (
        db.scalars(select(KpiMilestone).where(KpiMilestone.indicator_id.in_(ids))).all() if ids else []
    )
    return {
        "indicators": [indicator_view(i) for i in indicators],
        "milestones": [milestone_view(m) for m in milestones],
    }


def save_indicators(db: Session, batch_id: str, company: str, items: list[dict]) -> dict:
    for it in items:
        row = db.get(KpiIndicator, it["indicator_id"])
        if row is None or row.batch_id != batch_id or row.company != company:
            raise errors.not_found(f"指标不存在: {it.get('indicator_id')}")
        for k in ("content", "base_score", "max_score"):
            if k in it and it[k] is not None:
                setattr(row, k, str(it[k]))
        row.status = "filled"
    db.flush()
    return company_rows(db, batch_id, company)


def save_milestones(db: Session, batch_id: str, company: str, items: list[dict]) -> dict:
    valid_ids = {
        r[0]
        for r in db.execute(
            select(KpiIndicator.indicator_id).where(
                KpiIndicator.batch_id == batch_id, KpiIndicator.company == company
            )
        ).all()
    }
    for it in items:
        if it["indicator_id"] not in valid_ids:
            raise errors.not_found(f"指标不存在: {it.get('indicator_id')}")
        db.add(
            KpiMilestone(
                indicator_id=it["indicator_id"],
                content=it.get("content", ""),
                plan_date=it.get("plan_date", ""),
                material=it.get("material", ""),
            )
        )
    db.flush()
    return company_rows(db, batch_id, company)


def submit(db: Session, batch_id: str, company: str) -> dict:
    batch = get_batch(db, batch_id)
    rows = db.scalars(
        select(KpiIndicator).where(KpiIndicator.batch_id == batch_id, KpiIndicator.company == company)
    ).all()
    for r in rows:
        r.status = "filled"
    task = todo_svc.create_task(
        db, scene="kpi_fill", title=f"经营者考核审批（{company}）",
        dispatcher_id=batch.dispatcher_id, payload={"batch_id": batch_id}, period=batch.period,
    )
    todo_svc.create_todo(
        db, task=task, assignee_id=batch.dispatcher_id, kind="review",
        title=f"{company} · 经营者考核审批", sub=f"归属期：{batch.period}",
        ref={"batch_id": batch_id, "company": company},
    )
    db.flush()
    return company_rows(db, batch_id, company)


def review(db: Session, batch_id: str, company: str, approve: bool, comment: str | None) -> dict:
    rows = db.scalars(
        select(KpiIndicator).where(KpiIndicator.batch_id == batch_id, KpiIndicator.company == company)
    ).all()
    for r in rows:
        r.status = "reviewed" if approve else "unfilled"
    db.flush()
    return company_rows(db, batch_id, company)


def dispatch_ms_feedback(db: Session, *, period: str, dispatcher_id: str) -> list[KpiMsFeedback]:
    """对最新考核批次的全部里程碑发起反馈；无批次时按模板空批次创建。"""
    batch = db.scalars(select(KpiBatch).order_by(KpiBatch.created_at.desc()).limit(1)).first()
    if batch is None:
        raise errors.validation("尚无经营者考核批次，请先发起考核填报")
    milestones = db.scalars(
        select(KpiMilestone).join(KpiIndicator, KpiMilestone.indicator_id == KpiIndicator.indicator_id).where(
            KpiIndicator.batch_id == batch.batch_id
        )
    ).all()
    ind_map = {
        r[0]: r[1]
        for r in db.execute(
            select(KpiIndicator.indicator_id, KpiIndicator.company).where(KpiIndicator.batch_id == batch.batch_id)
        ).all()
    }
    task = todo_svc.create_task(
        db, scene="ms_feedback", title=f"里程碑反馈（{period}）",
        dispatcher_id=dispatcher_id, payload={"batch_id": batch.batch_id}, period=period,
    )
    out = []
    for m in milestones:
        company = ind_map.get(m.indicator_id, "")
        fb = KpiMsFeedback(milestone_id=m.milestone_id, batch_id=batch.batch_id, company=company)
        db.add(fb)
        db.flush()
        user = company_user(db, company)
        if user:
            todo_svc.create_todo(
                db, task=task, assignee_id=user.user_id, kind="action",
                title=f"{company} · 里程碑反馈", sub=f"归属期：{period} · {m.content[:30]}",
                ref={"feedback_id": fb.feedback_id, "company": company, "batch_id": batch.batch_id},
            )
        out.append(fb)
    return out


def list_feedbacks(db: Session, company: str | None = None) -> list[dict]:
    stmt = select(KpiMsFeedback).order_by(KpiMsFeedback.created_at.desc())
    if company:
        stmt = stmt.where(KpiMsFeedback.company == company)
    return [feedback_view(f) for f in db.scalars(stmt).all()]


def save_feedback(db: Session, feedback_id: str, data: dict) -> dict:
    f = db.get(KpiMsFeedback, feedback_id)
    if f is None:
        raise errors.not_found("反馈不存在")
    if f.review_status == "reviewed":
        raise errors.validation("已审批，不可修改")
    for k in ("status", "progress", "actual_date", "lamp", "status_note"):
        if k in data and data[k] is not None:
            setattr(f, k, data[k])
    db.flush()
    return feedback_view(f)


def submit_feedback(db: Session, feedback_id: str) -> dict:
    f = db.get(KpiMsFeedback, feedback_id)
    if f is None:
        raise errors.not_found("反馈不存在")
    f.review_status = "submitted"
    batch = db.get(KpiBatch, f.batch_id)
    if batch:
        task = todo_svc.create_task(
            db, scene="ms_feedback", title=f"里程碑反馈审批（{f.company}）",
            dispatcher_id=batch.dispatcher_id, payload={"feedback_id": feedback_id}, period=batch.period,
        )
        todo_svc.create_todo(
            db, task=task, assignee_id=batch.dispatcher_id, kind="review",
            title=f"{f.company} · 里程碑反馈审批", sub=f"亮灯：{f.lamp}",
            lamp=f.lamp, ref={"feedback_id": feedback_id, "company": f.company},
        )
    db.flush()
    return feedback_view(f)


def review_feedback(db: Session, feedback_id: str, approve: bool, comment: str | None) -> dict:
    f = db.get(KpiMsFeedback, feedback_id)
    if f is None:
        raise errors.not_found("反馈不存在")
    if f.review_status != "submitted":
        raise errors.validation("仅已提交状态可审批")
    f.review_status = "reviewed" if approve else "draft"
    db.flush()
    return feedback_view(f)


def adjust_lamp(
    db: Session, *, company: str, indicator_name: str, new_lamp: str, reason: str, operator: str
) -> KpiLampAdjustment:
    # 旧灯：最近一条同名里程碑反馈或默认绿
    old = db.scalars(
        select(KpiMsFeedback.lamp)
        .join(KpiMilestone, KpiMsFeedback.milestone_id == KpiMilestone.milestone_id)
        .where(KpiMsFeedback.company == company, KpiMilestone.content.contains(indicator_name))
        .order_by(KpiMsFeedback.created_at.desc())
        .limit(1)
    ).first()
    adj = KpiLampAdjustment(
        company=company, indicator_name=indicator_name,
        old_lamp=old or "g", new_lamp=new_lamp, reason=reason, operator=operator,
    )
    db.add(adj)
    db.flush()
    return adj
