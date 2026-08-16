from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.models import BizTodo, SysUser

# 11 家被投企业（seed 落库；inv01↔信投数科，其余按序）
COMPANIES = [
    "信投智造", "信投新能", "信投医疗", "信投数科", "信投物流", "信投环保",
    "信投半导", "信投云联", "信投金服", "信投教育", "信投文旅",
]

# inv 用户 → 公司
INV_COMPANY = {"inv01": "信投数科"}
_rest = [c for c in COMPANIES if c != "信投数科"]
for i, c in enumerate(_rest):
    INV_COMPANY[f"inv{i + 2:02d}"] = c


def list_companies(db: Session) -> list[str]:
    rows = db.scalars(
        select(SysUser.company).where(SysUser.role == "investee_finance").order_by(SysUser.username)
    ).all()
    return [c for c in rows if c]


def company_user(db: Session, company: str) -> SysUser | None:
    return db.scalars(
        select(SysUser).where(SysUser.role == "investee_finance", SysUser.company == company)
    ).first()


def dispatcher_name(db: Session, user_id: str) -> str:
    u = db.get(SysUser, user_id)
    return u.display_name if u else ""


def todo_view(db: Session, t: BizTodo) -> dict:
    return {
        "todo_id": t.todo_id,
        "task_id": t.task_id,
        "kind": t.kind,
        "scene": t.scene,
        "title": t.title,
        "sub": t.sub,
        "status": t.status,
        "lamp": t.lamp,
        "ref": t.ref or {},
        "dispatcher_name": dispatcher_name(db, _dispatcher_of(db, t.task_id)),
        "due": t.due.isoformat() if t.due else None,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


def _dispatcher_of(db: Session, task_id: str) -> str:
    from ..persistence.models import BizTask

    task = db.get(BizTask, task_id)
    return task.dispatcher_id if task else ""
