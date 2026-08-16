from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...persistence.models import KpiBatch, SysUser
from ...persistence.session import get_db
from ...services import kpi as kpi_svc
from .deps import current_user, require_hq

router = APIRouter(prefix="/kpi", tags=["kpi"])


def _batch_view(b: KpiBatch) -> dict:
    return {"batch_id": b.batch_id, "period": b.period, "dispatcher_id": b.dispatcher_id,
            "status": b.status, "created_at": b.created_at.isoformat()}


class CreateBatchIn(BaseModel):
    period: str


@router.post("/batches")
def create_batch(body: CreateBatchIn, user: SysUser = Depends(require_hq), db: Session = Depends(get_db)):
    return _batch_view(kpi_svc.create_batch(db, period=body.period, dispatcher_id=user.user_id))


@router.get("/batches")
def list_batches(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(KpiBatch).order_by(KpiBatch.created_at.desc())).all()
    return {"items": [_batch_view(b) for b in rows]}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return _batch_view(kpi_svc.get_batch(db, batch_id))


@router.get("/batches/{batch_id}/companies/{company}")
def company_rows(batch_id: str, company: str, user: SysUser = Depends(current_user),
                 db: Session = Depends(get_db)):
    return kpi_svc.company_rows(db, batch_id, company)


class IndicatorsIn(BaseModel):
    indicators: list[dict]


@router.put("/batches/{batch_id}/companies/{company}/indicators")
def save_indicators(batch_id: str, company: str, body: IndicatorsIn,
                    user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return kpi_svc.save_indicators(db, batch_id, company, body.indicators)


class MilestonesIn(BaseModel):
    milestones: list[dict]


@router.put("/batches/{batch_id}/companies/{company}/milestones")
def save_milestones(batch_id: str, company: str, body: MilestonesIn,
                    user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return kpi_svc.save_milestones(db, batch_id, company, body.milestones)


@router.post("/batches/{batch_id}/companies/{company}/submit")
def submit(batch_id: str, company: str, user: SysUser = Depends(current_user),
           db: Session = Depends(get_db)):
    return kpi_svc.submit(db, batch_id, company)


class ReviewIn(BaseModel):
    approve: bool
    comment: str | None = None


@router.post("/batches/{batch_id}/companies/{company}/review")
def review(batch_id: str, company: str, body: ReviewIn,
           user: SysUser = Depends(require_hq), db: Session = Depends(get_db)):
    return kpi_svc.review(db, batch_id, company, body.approve, body.comment)


class DispatchMsIn(BaseModel):
    period: str


@router.post("/ms-feedbacks/dispatch")
def dispatch_ms(body: DispatchMsIn, user: SysUser = Depends(require_hq), db: Session = Depends(get_db)):
    fbs = kpi_svc.dispatch_ms_feedback(db, period=body.period, dispatcher_id=user.user_id)
    return {"items": [kpi_svc.feedback_view(f) for f in fbs]}


@router.get("/ms-feedbacks")
def list_feedbacks(company: str | None = None, user: SysUser = Depends(current_user),
                   db: Session = Depends(get_db)):
    if user.role == "investee_finance":
        company = user.company
    return {"items": kpi_svc.list_feedbacks(db, company)}


class FeedbackIn(BaseModel):
    status: str
    progress: int
    actual_date: str | None = None
    lamp: str
    status_note: str | None = None


@router.put("/ms-feedbacks/{feedback_id}")
def save_feedback(feedback_id: str, body: FeedbackIn, user: SysUser = Depends(current_user),
                  db: Session = Depends(get_db)):
    return kpi_svc.save_feedback(db, feedback_id, body.model_dump())


@router.post("/ms-feedbacks/{feedback_id}/submit")
def submit_feedback(feedback_id: str, user: SysUser = Depends(current_user),
                    db: Session = Depends(get_db)):
    return kpi_svc.submit_feedback(db, feedback_id)


@router.post("/ms-feedbacks/{feedback_id}/review")
def review_feedback(feedback_id: str, body: ReviewIn, user: SysUser = Depends(require_hq),
                    db: Session = Depends(get_db)):
    return kpi_svc.review_feedback(db, feedback_id, body.approve, body.comment)


class LampAdjustIn(BaseModel):
    company: str
    indicator_name: str
    new_lamp: str
    reason: str


@router.post("/lamp-adjust")
def lamp_adjust(body: LampAdjustIn, user: SysUser = Depends(require_hq), db: Session = Depends(get_db)):
    adj = kpi_svc.adjust_lamp(db, company=body.company, indicator_name=body.indicator_name,
                              new_lamp=body.new_lamp, reason=body.reason, operator=user.user_id)
    return {"id": adj.id, "old_lamp": adj.old_lamp, "new_lamp": adj.new_lamp}
