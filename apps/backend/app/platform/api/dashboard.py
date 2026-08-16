from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import dashboard as dashboard_svc
from .deps import current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
def summary(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return dashboard_svc.summary(db)
