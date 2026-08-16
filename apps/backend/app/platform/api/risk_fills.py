from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...core import errors
from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import risk as risk_svc
from ...services.common import list_companies
from .deps import current_user, require_hq

router = APIRouter(prefix="/risk-fills", tags=["risk-fills"])


class CreateBatchIn(BaseModel):
    period: str


@router.post("")
def create_batch(body: CreateBatchIn, user: SysUser = Depends(require_hq), db: Session = Depends(get_db)):
    batch = risk_svc.create_batch(db, period=body.period, companies=list_companies(db),
                                  dispatcher_id=user.user_id)
    return risk_svc.batch_view(batch)


@router.get("")
def list_batches(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return {"items": risk_svc.list_batches(db)}


@router.get("/{batch_id}")
def get_batch(batch_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return risk_svc.get_batch(db, batch_id)


def _check_company(user: SysUser, company: str) -> None:
    if user.role == "investee_finance" and user.company != company:
        raise errors.forbidden("仅可操作本企业填报单")


@router.get("/{batch_id}/reports/{company}")
def get_report(batch_id: str, company: str, user: SysUser = Depends(current_user),
               db: Session = Depends(get_db)):
    _check_company(user, company)
    return risk_svc.get_report(db, batch_id, company)


class ItemsIn(BaseModel):
    items: list[dict]


@router.put("/{batch_id}/reports/{company}/items")
def save_items(batch_id: str, company: str, body: ItemsIn,
               user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    _check_company(user, company)
    return risk_svc.save_items(db, batch_id, company, body.items)


@router.post("/{batch_id}/reports/{company}/submit")
def submit(batch_id: str, company: str, user: SysUser = Depends(current_user),
           db: Session = Depends(get_db)):
    _check_company(user, company)
    return risk_svc.submit(db, batch_id, company)


class ReviewIn(BaseModel):
    approve: bool
    comment: str | None = None


@router.post("/{batch_id}/reports/{company}/review")
def review(batch_id: str, company: str, body: ReviewIn,
           user: SysUser = Depends(require_hq), db: Session = Depends(get_db)):
    return risk_svc.review(db, batch_id, company, body.approve, body.comment)
