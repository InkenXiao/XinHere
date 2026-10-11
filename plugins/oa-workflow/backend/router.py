"""OA REST 门面（EnvelopeRouter，前缀 /api/v1/oa；由平台 main.py 通用挂载 plugin_routers）。

统一 current_user 取平台登录身份；权限守卫走若依 perms（require_perm → 403 包封）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.persistence.models import SysUser
from app.persistence.session import get_db
from app.platform.api.deps import current_user
from app.services import todo as todo_svc

from . import perm as perm_svc
from . import service as oa_svc

router = APIRouter(prefix="/oa", tags=["oa"])


class RequestIn(BaseModel):
    template_key: str
    form_values: dict = Field(default_factory=dict)


class DispatchIn(BaseModel):
    title: str
    assignee_ids: list[str] = Field(min_length=1)
    note: str = ""
    due: str | None = None
    review_required: bool = False


class SubmitTodoIn(BaseModel):
    completion_text: str | None = None
    payload: dict | None = None


class AuditIn(BaseModel):
    approve: bool
    comment: str | None = None


def _parse_due(due: str | None):
    if not due:
        return None
    from datetime import datetime

    return datetime.fromisoformat(due)


@router.get("/me")
def oa_me(request: Request, user: SysUser = Depends(current_user)):
    """当前用户若依身份：用户/角色/权限串/菜单树/数据范围（前端按 perms 渲染按钮）。"""
    return perm_svc.me_view(user.user_id)


@router.get("/templates")
def oa_templates(request: Request, user: SysUser = Depends(current_user)):
    return oa_svc.oa_templates()


@router.get("/directory")
def oa_directory(request: Request, q: str = "", user: SysUser = Depends(current_user),
                 db: Session = Depends(get_db)):
    """OA 选人目录（若依数据范围过滤：scope=5 仅本人）。"""
    return perm_svc.directory(user.user_id, q=q)


@router.get("/my/todos")
def my_todos(request: Request, user: SysUser = Depends(current_user),
             db: Session = Depends(get_db)):
    perm_svc.require_perm(user.user_id, "oa:task:list")
    return oa_svc.my_todos(db, user.user_id)


@router.get("/my/approvals")
def my_approvals(request: Request, user: SysUser = Depends(current_user),
                 db: Session = Depends(get_db)):
    perm_svc.require_perm(user.user_id, "oa:approval:list")
    return oa_svc.my_approvals(db, user.user_id)


@router.get("/my/requests")
def my_requests(request: Request, user: SysUser = Depends(current_user),
                db: Session = Depends(get_db)):
    perm_svc.require_perm(user.user_id, "oa:request:list")
    return oa_svc.my_requests(db, user.user_id)


@router.post("/requests")
def create_request(body: RequestIn, request: Request,
                   user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """发起审批申请（建单+提交，待审批人处理）。"""
    perm_svc.require_perm(user.user_id, "oa:request:submit")
    return oa_svc.create_request(db, applicant_id=user.user_id,
                                 template_key=body.template_key,
                                 form_values=body.form_values)


@router.post("/tasks")
def dispatch_task(body: DispatchIn, request: Request,
                  user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """派发 OA 任务（含给自己=个人待办）。"""
    others = [i for i in body.assignee_ids if str(i) != str(user.user_id)]
    if others:
        perm_svc.require_perm(user.user_id, "oa:task:dispatch")
    return oa_svc.dispatch_task(db, dispatcher_id=user.user_id, title=body.title,
                                assignee_ids=body.assignee_ids, note=body.note,
                                due=_parse_due(body.due), review_required=body.review_required)


@router.post("/todos/{todo_id}/payload")
def save_payload(todo_id: str, body: SubmitTodoIn, request: Request,
                 user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """草稿保存（仅承办人本人，内核守卫）。"""
    return todo_svc.save_payload(db, todo_id, user.user_id, body.payload or {})


@router.post("/todos/{todo_id}/submit")
def submit_todo(todo_id: str, body: SubmitTodoIn, request: Request,
                user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """提交待办/申请（审核任务→submitted 待审，否则直接 completed）。"""
    return todo_svc.submit(db, todo_id, user.user_id,
                           completion_text=body.completion_text, payload=body.payload)


@router.post("/approvals/{todo_id}")
def audit(todo_id: str, body: AuditIn, request: Request,
          user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """审批：通过/退回（退回须意见；内核守卫仅任务审批人可操作）。"""
    perm_svc.require_perm(user.user_id, "oa:approval:audit")
    return oa_svc.audit(db, todo_id, user.user_id, body.approve, body.comment)
