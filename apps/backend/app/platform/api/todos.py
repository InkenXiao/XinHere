from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import todo as todo_svc
from .deps import current_user

router = APIRouter(prefix="/todos", tags=["todos"])


@router.get("")
def list_todos(box: str = Query("assignee", pattern="^(assignee|dispatcher)$"),
               user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return {"items": todo_svc.list_todos(db, user, box)}


class FeedbackIn(BaseModel):
    text: str


@router.post("/{todo_id}/feedback")
def feedback(todo_id: str, body: FeedbackIn, user: SysUser = Depends(current_user),
             db: Session = Depends(get_db)):
    return todo_svc.feedback(db, todo_id, body.text)


class NaIn(BaseModel):
    reason: str


@router.post("/{todo_id}/na")
def na(todo_id: str, body: NaIn, user: SysUser = Depends(current_user),
       db: Session = Depends(get_db)):
    return todo_svc.na(db, todo_id, body.reason)


@router.post("/{todo_id}/na-confirm")
def na_confirm(todo_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return todo_svc.na_confirm(db, todo_id)


class NaRejectIn(BaseModel):
    comment: str | None = None


@router.post("/{todo_id}/na-reject")
def na_reject(todo_id: str, body: NaRejectIn, user: SysUser = Depends(current_user),
              db: Session = Depends(get_db)):
    return todo_svc.na_reject(db, todo_id, body.comment)


@router.post("/{todo_id}/complete")
def complete(todo_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return todo_svc.complete(db, todo_id)
