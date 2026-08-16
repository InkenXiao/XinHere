from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import report as report_svc
from .deps import current_user

router = APIRouter(prefix="/reports", tags=["reports"])


class CreateIn(BaseModel):
    company_ids: list[str]
    period: str


@router.post("")
def create(body: CreateIn, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    r = report_svc.create(db, company_ids=body.company_ids, period=body.period)
    report_id = r.report_id
    db.flush()
    report_svc.generate_async(report_id)  # 异步生成；工具触发路径经事件族推送进度
    return {"report_id": report_id}


@router.get("")
def list_reports(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return {"items": report_svc.list_reports(db)}


@router.get("/{report_id}")
def get_report(report_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return report_svc.get(db, report_id)
