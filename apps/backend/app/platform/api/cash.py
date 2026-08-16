from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...core import errors
from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import cash as cash_svc
from .deps import current_user, require_hq

router = APIRouter(prefix="/cash-guarantees", tags=["cash-guarantees"])


class CreateIn(BaseModel):
    company: str
    period: str


@router.post("")
def create(body: CreateIn, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    if user.role == "investee_finance" and user.company != body.company:
        raise errors.forbidden("仅可操作本企业单据")
    form, _prev = cash_svc.create_form(db, company=body.company, period=body.period,
                                       dispatcher_id=user.user_id)
    return cash_svc.view(form)


@router.get("")
def list_forms(company: str | None = None, period: str | None = None,
               user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    if user.role == "investee_finance":
        company = user.company
    return {"items": cash_svc.list_forms(db, company, period)}


@router.get("/{form_id}")
def get_form(form_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return cash_svc.view(cash_svc.get(db, form_id))


class DraftIn(BaseModel):
    avail_cash: float
    pooled_fund: float
    avail_credit: float
    monthly_outflow: float


@router.put("/{form_id}")
def save_draft(form_id: str, body: DraftIn, user: SysUser = Depends(current_user),
               db: Session = Depends(get_db)):
    return cash_svc.save_draft(db, form_id, body.model_dump())


@router.post("/{form_id}/submit")
def submit(form_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    form = cash_svc.get(db, form_id)
    dispatcher = user.user_id if user.role == "hq_finance" else _hq_of(db)
    return cash_svc.submit(db, form_id, dispatcher_id=dispatcher or user.user_id)


def _hq_of(db: Session) -> str | None:
    from sqlalchemy import select

    hq = db.scalars(select(SysUser).where(SysUser.role == "hq_finance").limit(1)).first()
    return hq.user_id if hq else None


class ReviewIn(BaseModel):
    approve: bool
    comment: str | None = None


@router.post("/{form_id}/review")
def review(form_id: str, body: ReviewIn, user: SysUser = Depends(require_hq),
           db: Session = Depends(get_db)):
    return cash_svc.review(db, form_id, body.approve, body.comment)
