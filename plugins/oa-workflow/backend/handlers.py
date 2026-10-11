"""OA 组件卡处理器（component/submit 业务落库；签名对齐平台 HANDLERS 契约：
(db, *, session_id, user_id, props, values) -> (summary, biz_events)）。

kind：oa-dispatch-confirm（派发确认）/ oa-request-card（审批申请表单）/ oa-audit-card（审批）。
cancel 动作由 sessions._do_component_submit 前置拦截，处理器只承接真实提交。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core import errors

from . import perm as perm_svc
from . import service as oa_svc

logger = logging.getLogger(__name__)


def handle_oa_dispatch_confirm(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list]:
    """OA 派发确认卡：values.assignee_ids 勾选过滤（同 arrange-confirm）；
    派给他人须 oa:task:dispatch 权限，纯个人待办（只派给自己）登录即可。"""
    assignees = props.get("assignees") or []
    if values and values.get("assignee_ids") is not None:
        want = {str(i) for i in values["assignee_ids"]}
        assignees = [a for a in assignees if str(a.get("user_id")) in want]
    if not assignees:
        raise errors.validation("承办人为空，无法派发")
    title = str(props.get("title") or "").strip()
    if not title:
        raise errors.validation("任务标题不能为空")
    ids = [str(a["user_id"]) for a in assignees]
    others = [i for i in ids if i != str(user_id)]
    if others:
        perm_svc.require_perm(user_id, "oa:task:dispatch")
    due = None
    if props.get("due"):
        from datetime import datetime as _dt

        due = _dt.fromisoformat(str(props["due"]))
    r = oa_svc.dispatch_task(
        db, dispatcher_id=user_id, title=title, assignee_ids=ids,
        note=str(props.get("note") or ""), due=due,
        review_required=bool(props.get("review_required")), session_id=session_id,
    )
    logger.info("OA 派发 sid=%s user=%s task=%s 人数=%d", session_id, user_id, r["task_id"], r["count"])
    if not others:
        return f"已为你创建个人待办「{title}」", []
    return f"已派发 OA 任务「{title}」给 {r['names']}", []


def handle_oa_request_card(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list]:
    """OA 审批申请表单卡：values.form_values 提交 → 建单+提交待审批。"""
    if not values or not values.get("form_values"):
        raise errors.validation("请填写申请表单")
    perm_svc.require_perm(user_id, "oa:request:submit")
    v = oa_svc.create_request(
        db, applicant_id=user_id, template_key=str(props.get("template_key") or ""),
        form_values=values["form_values"], session_id=session_id,
    )
    logger.info("OA 申请提交 sid=%s user=%s tpl=%s todo=%s",
                session_id, user_id, props.get("template_key"), v["todo_id"])
    name = props.get("template_name") or "审批单"
    return (f"{name}已提交，等待 {v.get('approver_name') or '审批人'} 审批"
            f"（单号 {str(v['todo_id'])[:8]}）"), []


def handle_oa_audit_card(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list]:
    """OA 审批卡：decisions=[{todo_id, action: approve|reject|skip, comment}]（同 todo-review-card 范式）。"""
    decisions = (values or {}).get("decisions") or []
    if not decisions:
        raise errors.validation("没有审批决定")
    perm_svc.require_perm(user_id, "oa:approval:audit")
    ok = rej = skip = 0
    for d in decisions:
        action = str(d.get("action") or "")
        if action == "skip":
            skip += 1
            continue
        todo_id = str(d.get("todo_id") or "")
        if action == "approve":
            oa_svc.audit(db, todo_id, user_id, True, d.get("comment"))
            ok += 1
        elif action == "reject":
            c = str(d.get("comment") or "").strip()
            if not c:
                raise errors.validation("退回须填写审批意见")
            oa_svc.audit(db, todo_id, user_id, False, c)
            rej += 1
    logger.info("OA 审批 sid=%s user=%s 通过=%d 退回=%d 跳过=%d", session_id, user_id, ok, rej, skip)
    parts = [f"通过 {ok} 项"]
    if rej:
        parts.append(f"退回 {rej} 项")
    if skip:
        parts.append(f"跳过 {skip} 项")
    return "审批完成：" + "、".join(parts), []
