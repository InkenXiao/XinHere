"""OA 业务服务：复用平台 BizTask/BizTodo 内核（scene="oa"，不传 period/lamp），
插件→平台单向依赖，删除插件无数据/代码残留。

审批模型（最小版·单审批人）：
- 审批单 = 模板任务（oa_leave/oa_expense/oa_generic）：dispatcher=审批人（按模板
  approver_role 从若依目录解析），assignee=申请人，review_required=True；
  发起即建单+填表+提交（status=submitted），审批人 通过→completed / 退回→returned。
- 派发任务 = 通用任务（无模板）：dispatcher=派发人，assignee=承办人；
  派给自己即「个人待办」（review_required 默认 False，提交即完成）。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import errors
from app.persistence.models import BizTask, BizTodo
from app.services import identity as identity_svc
from app.services import task_templates
from app.services import todo as todo_svc
from app.services.common import todo_view

from . import perm as perm_svc

SCENE = "oa"


# ---------------- 模板 ----------------


def oa_templates() -> list[dict]:
    """OA 审批模板目录（scene=oa 的任务模板 + 审批人角色说明）。"""
    out = []
    for v in task_templates.list_template_views():
        if v["scene"] != SCENE:
            continue
        tpl = task_templates.get_template(v["template_key"])
        out.append({**v, "approver_role": tpl.get("approver_role") or "dept_leader",
                    "form": tpl["form"]})
    return out


def _tpl(template_key: str) -> dict:
    tpl = task_templates.get_template(template_key)
    if tpl.get("scene") != SCENE:
        raise errors.validation(f"模板 {template_key} 不是 OA 审批模板")
    return tpl


# ---------------- 显示名（若依目录优先，平台 identity 兜底） ----------------


def _names(uids) -> dict[str, str]:
    out = perm_svc.user_names(uids)
    missing = [u for u in map(str, uids) if u not in out]
    if missing:
        try:
            out.update(identity_svc.user_names(missing))
        except Exception:
            pass
    return out


def _name(uid) -> str:
    return _names([uid]).get(str(uid), str(uid))


# ---------------- 审批单（申请 → 提交 → 审批） ----------------


def create_request(db: Session, *, applicant_id: str, template_key: str, form_values: dict,
                   session_id: str | None = None) -> dict:
    """发起审批：建模板任务（审批人=派发人）→ 写表 → 提交（submitted 待审批）。"""
    tpl = _tpl(template_key)
    task_templates.validate_payload(tpl, form_values or {})
    role_key = tpl.get("approver_role") or "dept_leader"
    approver = perm_svc.resolve_approver(role_key, exclude_uid=applicant_id)
    if approver is None:
        raise errors.validation(
            f"「{tpl['name']}」暂无可用审批人（若依角色 {role_key}），请联系管理员")
    r = todo_svc.spawn_task(
        db, template_key=template_key, dispatcher_id=str(approver["user_id"]),
        assignee_ids=[str(applicant_id)],
        anchor={"applicant": _name(applicant_id)}, trigger="oa",
    )
    todo = db.scalars(select(BizTodo).where(BizTodo.task_id == r["task_id"])).first()
    todo.title = f"{tpl['name']} · {_name(applicant_id)}"
    todo.sub = f"审批人：{approver['nick_name']}"
    todo.ref = {**(todo.ref or {}), "oa_applicant": str(applicant_id), "oa_kind": "request"}
    todo.payload = dict(form_values or {})
    if session_id:
        todo.session_id = uuid.UUID(str(session_id))
    out = todo_svc.submit(db, todo.todo_id, str(applicant_id),
                          completion_text=None, payload=None, session_id=session_id)
    out["approver_name"] = approver["nick_name"]
    return out


def audit(db: Session, todo_id: str, approver_id: str, approve: bool,
          comment: str | None = None) -> dict:
    """审批：内核 review 守卫（仅任务派发人=审批人可操作）。
    权限串守卫（oa:approval:audit）由调用方（REST/卡处理器）负责。"""
    t = db.get(BizTodo, todo_id)
    if t is None:
        raise errors.not_found("审批单不存在")
    task = db.get(BizTask, t.task_id)
    if task is None or task.scene != SCENE or not task.template_key:
        raise errors.validation("该待办不是 OA 审批单")
    if not approve and not (comment or "").strip():
        raise errors.validation("退回须填写审批意见")
    return todo_svc.review(db, todo_id, str(approver_id), approve, comment)


def my_approvals(db: Session, uid) -> list[dict]:
    """待我审批：我名下 OA 模板任务的 submitted 待办。"""
    rows = db.scalars(
        select(BizTodo).join(BizTask, BizTodo.task_id == BizTask.task_id)
        .where(BizTask.dispatcher_id == str(uid), BizTask.scene == SCENE,
               BizTask.template_key.isnot(None), BizTodo.status == "submitted")
        .order_by(BizTodo.submitted_at.asc()).limit(50)
    ).all()
    names = _names([(r.ref or {}).get("oa_applicant") or r.assignee_id for r in rows])
    out = []
    for t in rows:
        v = todo_view(db, t, include_payload=True)
        v["applicant_name"] = names.get(str((t.ref or {}).get("oa_applicant") or t.assignee_id),
                                        _name(t.assignee_id))
        v["submitted_at"] = t.submitted_at.isoformat() if t.submitted_at else None
        out.append(v)
    return out


def my_requests(db: Session, uid) -> list[dict]:
    """我发起的审批单：全部状态（草稿/待审批/已通过/已退回）。"""
    rows = db.scalars(
        select(BizTodo).join(BizTask, BizTodo.task_id == BizTask.task_id)
        .where(BizTodo.assignee_id == str(uid), BizTodo.scene == SCENE, BizTodo.kind == "action",
               BizTask.template_key.isnot(None), BizTodo.status != "na_closed")
        .order_by(BizTodo.created_at.desc()).limit(50)
    ).all()
    out = []
    for t in rows:
        v = todo_view(db, t, include_payload=True)
        v["submitted_at"] = t.submitted_at.isoformat() if t.submitted_at else None
        out.append(v)
    return out


# ---------------- 任务派发 / 个人待办 ----------------


def dispatch_task(db: Session, *, dispatcher_id: str, title: str, assignee_ids: list,
                  note: str = "", due=None, review_required: bool = False,
                  session_id: str | None = None) -> dict:
    """派发 OA 任务（assignee 含自己=个人待办；权限守卫由调用方负责）。"""
    title = (title or "").strip()
    if not title:
        raise errors.validation("任务标题不能为空")
    ids = [str(i) for i in (assignee_ids or [])]
    if not ids:
        raise errors.validation("请选择承办人")
    task = todo_svc.create_task(
        db, scene=SCENE, title=title, dispatcher_id=str(dispatcher_id),
        payload={"content": note}, review_required=review_required, due=due,
        spawn_meta={"trigger": "oa"},
    )
    for uid in ids:
        todo_svc.create_todo(
            db, task=task, assignee_id=uid, kind="action", title=title,
            sub=note or "OA 派发任务", ref={"source": "oa_dispatch", "content": note},
            due=due, session_id=uuid.UUID(str(session_id)) if session_id else None,
        )
    return {"task_id": task.task_id, "title": title, "count": len(ids),
            "names": "、".join(_names(ids).get(u, u) for u in ids)}


def my_todos(db: Session, uid) -> list[dict]:
    """我的 OA 待办：派给我的 OA 任务（模板审批单另见 my_requests）。"""
    rows = db.scalars(
        select(BizTodo).join(BizTask, BizTodo.task_id == BizTask.task_id)
        .where(BizTodo.assignee_id == str(uid), BizTodo.scene == SCENE, BizTodo.kind == "action",
               BizTask.template_key.is_(None), BizTodo.status.in_(todo_svc.ACTIVE_STATUSES))
        .order_by(BizTodo.created_at.desc()).limit(50)
    ).all()
    tasks = {t.task_id: t for t in (db.get(BizTask, r.task_id) for r in rows)}
    names = _names([tk.dispatcher_id for tk in tasks.values()])
    out = []
    for t in rows:
        v = todo_view(db, t)
        task = tasks.get(t.task_id)
        v["assigner_name"] = names.get(str(task.dispatcher_id), "") if task else ""
        out.append(v)
    return out
