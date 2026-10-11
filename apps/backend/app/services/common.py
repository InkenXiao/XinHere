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
    if u is not None:
        return u.display_name
    # 主库无行（SCMS 透传真实用户）→ 业务库 system_users 取昵称
    from . import identity as identity_svc

    return identity_svc.user_names([user_id]).get(str(user_id), "")


def todo_view(db: Session, t: BizTodo, include_payload: bool = False) -> dict:
    from ..persistence.models import BizTask
    from .todo import derive_overdue, period_locked

    task = db.get(BizTask, t.task_id)
    locked = period_locked(task) if task else False
    template_key = task.template_key if task else None
    dynamic_template_id = task.dynamic_template_id if task else None  # 非空 → 动态 HTML 填报
    related_system = task.related_system if task else None
    anchor: dict = ((task.spawn_meta or {}).get("anchor") or {}) if task else {}
    form = (task.payload or {}).get("form") if task else None
    return {
        "todo_id": t.todo_id,
        "task_id": t.task_id,
        "assignee_id": t.assignee_id,
        "assignee_name": dispatcher_name(db, t.assignee_id),
        "kind": t.kind,
        "scene": t.scene,
        "title": t.title,
        "sub": t.sub,
        "status": t.status,
        "lamp": t.lamp,
        "lamp_agg": t.lamp_agg or {},
        "ref": t.ref or {},
        "payload": (t.payload or {}) if include_payload else None,
        "template_key": template_key,
        "dynamic_template_id": dynamic_template_id,
        "related_system": related_system,
        "anchor": anchor,
        "form": form,
        "completion_text": t.completion_text,
        "submitted_at": t.submitted_at.isoformat() if t.submitted_at else None,
        "returned_at": t.returned_at.isoformat() if t.returned_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "return_reason": t.return_reason,
        "remind_count": t.remind_count,
        "last_reminded_at": t.last_reminded_at.isoformat() if t.last_reminded_at else None,
        "review_required": bool(task.review_required) if task else False,
        "period_locked": locked,
        "overdue": derive_overdue(t, task) if task else False,  # 未填报（入口隐藏）
        "session_id": str(t.session_id) if t.session_id else None,
        "dispatcher_name": dispatcher_name(db, task.dispatcher_id if task else ""),
        "due": t.due.isoformat() if t.due else None,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


def _dispatcher_of(db: Session, task_id: str) -> str:
    from ..persistence.models import BizTask

    task = db.get(BizTask, task_id)
    return task.dispatcher_id if task else ""
