from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import errors
from ..persistence.models import (
    BizTask,
    BizTodo,
    TaskLampLog,
    TaskRemark,
    SysUser,
)
from .common import todo_view

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("pending", "returned", "na_pending", "submitted")
TERMINAL_STATUSES = ("completed", "na_closed")
# 期间锁定后仍可见的只读态（overdue 为派生态，见 period_locked/derive_overdue）

MAX_TREE_DEPTH = 4  # 任务树深度上限（数据→方案→应对项 等级链防失控）


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------- 派发（task 层） ----------------


def create_task(
    db: Session, *, scene: str, title: str, dispatcher_id: str, payload: dict | None = None,
    period: str | None = None,
    template_key: str | None = None,
    dynamic_template_id: str | None = None,
    parent_task_id: str | None = None,
    related_system: str | None = None,
    review_required: bool = False,
    due=None,
    end_policy: str = "all_done",
    spawn_meta: dict | None = None,
) -> BizTask:
    if parent_task_id:
        parent = db.get(BizTask, parent_task_id)
        if parent is None:
            raise errors.not_found("父任务不存在")
        if parent.status != "open":
            raise errors.validation("父任务已结束，不能再派生子流程")
        depth, node = 1, parent
        while node.parent_task_id and depth < MAX_TREE_DEPTH + 2:
            node = db.get(BizTask, node.parent_task_id)  # type: ignore[assignment]
            if node is None:
                break
            depth += 1
        if depth > MAX_TREE_DEPTH:
            raise errors.validation(f"任务树深度超过 {MAX_TREE_DEPTH} 层，不允许继续派生")
    task = BizTask(
        scene=scene, title=title, dispatcher_id=dispatcher_id, payload=payload or {},
        period=period, template_key=template_key, dynamic_template_id=dynamic_template_id,
        parent_task_id=parent_task_id,
        related_system=related_system, review_required=review_required, due=due,
        end_policy=end_policy, spawn_meta=spawn_meta or {},
    )
    db.add(task)
    db.flush()
    return task


def create_todo(
    db: Session, *, task: BizTask, assignee_id: str, kind: str = "action", title: str | None = None,
    sub: str = "", lamp: str | None = None, ref: dict | None = None, due=None,
    payload: dict | None = None, status: str = "pending", session_id=None,
) -> BizTodo:
    """建待办。status 参数用于告警内嵌场景（直建 submitted，跳过待填报）。"""
    if status not in ("pending", "submitted"):
        raise errors.validation(f"建单仅允许 pending/submitted 初始态，收到 {status}")
    todo = BizTodo(
        task_id=task.task_id,
        assignee_id=assignee_id,
        kind=kind,
        scene=task.scene,
        title=title or task.title,
        sub=sub,
        lamp=lamp,
        ref=ref or {},
        due=due or task.due,
        payload=payload or {},
        status=status,
        submitted_at=_now() if status == "submitted" else None,
        session_id=session_id,
    )
    db.add(todo)
    db.flush()
    _notify_todo(db, todo)
    return todo


def list_todos(db: Session, user: SysUser, box: str) -> list[dict]:
    if box == "assignee":
        rows = db.scalars(
            select(BizTodo)
            .where(BizTodo.assignee_id == user.user_id, BizTodo.status != "na_closed")
            .order_by(BizTodo.created_at.desc())
        ).all()
    else:
        task_ids = select(BizTask.task_id).where(BizTask.dispatcher_id == user.user_id)
        rows = db.scalars(
            select(BizTodo)
            .where(BizTodo.task_id.in_(task_ids), BizTodo.status != "na_closed")
            .order_by(BizTodo.created_at.desc())
        ).all()
    return [todo_view(db, t) for t in rows]


def dispatch_queue(db: Session, user_id: str, statuses: list[str]) -> list[dict]:
    """派发人工作队列：本人派发的 open 任务下、处于指定状态的待办（AI 逐条审核/确认/催办取数）。"""
    task_ids = select(BizTask.task_id).where(
        BizTask.dispatcher_id == user_id, BizTask.status == "open"
    )
    rows = db.scalars(
        select(BizTodo)
        .where(BizTodo.task_id.in_(task_ids), BizTodo.status.in_(statuses))
        .order_by(BizTodo.created_at)
    ).all()
    out = []
    for t in rows:
        v = todo_view(db, t)
        v["na_reason"] = t.na_reason
        v["na_at"] = t.na_at.isoformat() if t.na_at else None
        v["assignee_name"] = _display_name(db, t.assignee_id)
        v["submitted_at"] = t.submitted_at.isoformat() if t.submitted_at else None
        out.append(v)
    return out


def _get(db: Session, todo_id: str) -> BizTodo:
    t = db.get(BizTodo, todo_id)
    if t is None:
        raise errors.not_found("待办不存在")
    return t


def _task_of(db: Session, t: BizTodo) -> BizTask:
    task = db.get(BizTask, t.task_id)
    if task is None:
        raise errors.not_found("所属任务不存在")
    return task


def _notify_todo(db: Session, t: BizTodo) -> None:
    """内核流转 → todo/changed 触达（assignee + dispatcher）：SSE 订阅者实时收，其余 30s 轮询兜底。
    触达失败不回滚业务流转。"""
    from ..platform.events.todo_bus import publish

    try:
        task = db.get(BizTask, t.task_id)
        if task is None:
            return
        publish([t.assignee_id, task.dispatcher_id], "todo/changed", {
            "todo_id": t.todo_id, "task_id": t.task_id,
            "status": t.status, "title": t.title,
        })
    except Exception:
        logger.warning("todo/changed 发布失败 todo=%s", t.todo_id, exc_info=True)


def _create_notification(
    db: Session, t: BizTodo, *, recipient_id: str, actor_id: str,
    category: str, title: str, body: str,
) -> None:
    from . import notification as notification_svc

    notification_svc.create(
        db, recipient_id=recipient_id, actor_id=actor_id, category=category,
        title=title, body=body, todo_id=t.todo_id, task_id=t.task_id,
    )


def _require_assignee(t: BizTodo, user_id: str) -> None:
    if t.assignee_id != user_id:
        raise errors.forbidden("仅承办人本人可操作")

def _require_dispatcher(task: BizTask, user_id: str) -> None:
    if task.dispatcher_id != user_id:
        raise errors.forbidden("仅派发人可操作")


# ---------------- 存量路径（保留：反馈 / 不涉及） ----------------


def _dispatcher_review_todo(db: Session, src: BizTodo, kind: str, sub_suffix: str) -> None:
    task = db.get(BizTask, src.task_id)
    if task is None:
        return
    create_todo(
        db,
        task=task,
        assignee_id=task.dispatcher_id,
        kind=kind,
        title=f"{'不涉及确认' if kind == 'na_confirm' else '反馈复核'}：{src.title}",
        sub=sub_suffix,
        ref={**src.ref, "src_todo_id": src.todo_id},
    )


def feedback(db: Session, todo_id: str, text: str) -> dict:
    t = _get(db, todo_id)
    if t.status not in ("pending",):
        raise errors.validation(f"当前状态 {t.status} 不允许反馈")
    t.feedback_text = text
    t.status = "feedback_submitted"
    _dispatcher_review_todo(db, t, "feedback_review", f"反馈：{text[:50]}")
    db.flush()
    return todo_view(db, t)


def na(db: Session, todo_id: str, reason: str) -> dict:
    t = _get(db, todo_id)
    if t.status not in ("pending", "returned"):
        raise errors.validation(f"当前状态 {t.status} 不允许标记不涉及")
    t.na_reason = reason
    t.status = "na_pending"
    t.na_at = _now()
    _dispatcher_review_todo(db, t, "na_confirm", f"不涉及原因：{reason[:50]}")
    db.flush()
    _notify_todo(db, t)
    return todo_view(db, t)


def na_confirm(db: Session, todo_id: str) -> dict:
    t = _get(db, todo_id)
    if t.status != "na_pending":
        raise errors.validation("仅 na_pending 状态可确认")
    t.status = "na_closed"
    db.flush()
    _notify_todo(db, t)
    return todo_view(db, t)


def na_reject(db: Session, todo_id: str, comment: str | None) -> dict:
    """不涉及驳回 = 再次派发：回 pending 并留再次派发说明（处理人侧展示）。"""
    t = _get(db, todo_id)
    if t.status != "na_pending":
        raise errors.validation("仅 na_pending 状态可驳回")
    t.status = "pending"
    t.na_comment = comment
    t.redispatch_reason = comment
    db.flush()
    _notify_todo(db, t)
    return todo_view(db, t)


# ---------------- 内核：两段式 审核 ----------------


def save_payload(db: Session, todo_id: str, user_id: str, payload: dict) -> dict:
    """草稿保存：仅 pending/returned 且承办人本人可写（提交后锁定）。"""
    t = _get(db, todo_id)
    _require_assignee(t, user_id)
    if t.status not in ("pending", "returned"):
        raise errors.validation(f"当前状态 {t.status} 不允许修改填报内容")
    t.payload = payload
    db.flush()
    return todo_view(db, t)


def submit(
    db: Session, todo_id: str, user_id: str,
    completion_text: str | None = None, payload: dict | None = None,
    session_id=None, embed_plan: dict | None = None,
) -> dict:
    """承办人提交（填报/完成说明）。
    task.review_required=True → submitted 待审核；False → 直接 completed（兼容存量）。
    payload 传入则随提交覆盖（仅 pending/returned 可达此处）。
    embed_plan：告警填报同步填方案（已确认口径=方案）→ 子 todo 直建 submitted，跳过"待填报方案"态。"""
    t = _get(db, todo_id)
    _require_assignee(t, user_id)
    if t.status not in ("pending", "returned"):
        raise errors.validation(f"当前状态 {t.status} 不允许提交")
    task = _task_of(db, t)
    if payload is not None:
        t.payload = payload
    t.completion_text = completion_text or t.completion_text
    t.session_id = session_id or t.session_id
    t.lamp_agg = _count_lamps(t.payload)
    t.submitted_at = _now()
    # 通用任务的办理提交始终进入派发人审核；兼容历史上错误关闭审核开关的任务。
    requires_review = task.review_required or task.scene == "generic"
    t.status = "submitted" if requires_review else "completed"
    if t.status == "completed":
        t.completed_at = _now()
    db.flush()
    _notify_todo(db, t)
    _create_notification(
        db, t, recipient_id=task.dispatcher_id, actor_id=user_id,
        category="todo_completed" if t.status == "completed" else "todo_submitted",
        title=f"{t.title}{'已完成' if t.status == 'completed' else '已提交'}",
        body="承办人已完成该任务。" if t.status == "completed" else "承办人已完成办理并提交，请及时审核。",
    )
    out = todo_view(db, t)
    if embed_plan:
        out["spawned"] = _spawn_embedded_plan(db, t, task, embed_plan)
    return out


def _spawn_embedded_plan(db: Session, todo: BizTodo, task: BizTask, plan: dict) -> dict:
    """告警内嵌（2026 确认：填"方案"）：提交数据填报时同步收集方案，
    子流程 todo 直建 submitted（正常流程变为 待审核→已审核数据→待审批方案）。"""
    from . import task_templates

    tpl = task_templates.get_template(plan.get("template_key", "risk_plan"))
    plan_payload = plan.get("payload") or {}
    task_templates.validate_payload(tpl, plan_payload)
    anchor = dict((task.spawn_meta or {}).get("anchor") or {})
    anchor["company"] = anchor.get("company") or todo.ref.get("company") or ""
    child = spawn_task(
        db, template_key=tpl["template_key"], dispatcher_id=task.dispatcher_id,
        assignee_ids=[todo.assignee_id], period=task.period, anchor=anchor,
        parent_task_id=task.task_id, trigger="alarm_embedded", reuse_mode="fresh",
    )
    child_todo = db.scalars(select(BizTodo).where(BizTodo.task_id == child["task_id"])).first()
    child_todo.payload = plan_payload
    child_todo.lamp_agg = _count_lamps(plan_payload)
    child_todo.submitted_at = _now()
    child_todo.status = "submitted"
    child_todo.session_id = todo.session_id
    db.flush()
    return todo_view(db, child_todo)


def _next_embedded_review(db: Session, task: BizTask) -> dict | None:
    """双弹窗审核接力：数据审核通过后，找告警内嵌子流程中待审批的方案 todo。"""
    children = db.scalars(select(BizTask).where(BizTask.parent_task_id == task.task_id)).all()
    for c in children:
        if (c.spawn_meta or {}).get("trigger") != "alarm_embedded":
            continue
        row = db.scalars(
            select(BizTodo).where(BizTodo.task_id == c.task_id, BizTodo.status == "submitted")
        ).first()
        if row is not None:
            return todo_view(db, row, include_payload=True)
    return None


def review(db: Session, todo_id: str, user_id: str, approve: bool, comment: str | None = None) -> dict:
    """审核方（派发人）审核：通过 → completed；退回 → returned（数据保留，意见处理人侧展示）。"""
    t = _get(db, todo_id)
    task = _task_of(db, t)
    _require_dispatcher(task, user_id)
    if t.status != "submitted":
        raise errors.validation(f"当前状态 {t.status} 不允许审核")
    if approve:
        t.status = "completed"
        t.completed_at = _now()
    else:
        t.status = "returned"
        t.returned_at = _now()
        t.return_reason = comment or ""
    db.flush()
    _notify_todo(db, t)
    _create_notification(
        db, t, recipient_id=t.assignee_id, actor_id=user_id,
        category="todo_completed" if approve else "todo_returned",
        title=f"{t.title}{'已完成' if approve else '已退回'}",
        body="任务审核已通过。" if approve else f"退回原因：{comment or '请补充完善后重新提交'}",
    )
    out = todo_view(db, t)
    if approve:
        nxt = _next_embedded_review(db, task)  # 双弹窗：数据审核 → 方案审核接力
        if nxt is not None and nxt["todo_id"] != t.todo_id:
            out["next_review"] = nxt
    return out


def complete(db: Session, todo_id: str, user_id: str | None = None) -> dict:
    """存量直接完成（review_required=False 的任务）。两段式任务须走 submit/review。"""
    t = _get(db, todo_id)
    actor_id = user_id or t.assignee_id
    _require_assignee(t, actor_id)
    if t.status in TERMINAL_STATUSES:
        raise errors.validation("待办已终结")
    task = _task_of(db, t)
    if task.review_required:
        raise errors.validation("本任务需审核，请走 提交(submit)/审核(review) 流程")
    t.status = "completed"
    t.completed_at = _now()
    db.flush()
    _notify_todo(db, t)
    _create_notification(
        db, t, recipient_id=task.dispatcher_id, actor_id=actor_id,
        category="todo_completed", title=f"{t.title}已完成",
        body="承办人已完成该任务。",
    )
    return todo_view(db, t)


# ---------------- 派发小流程（模板驱动：方案/应对项…） ----------------


def spawn_task(
    db: Session, *, template_key: str, dispatcher_id: str, assignee_ids: list[str],
    period: str | None = None, anchor: dict | None = None, parent_task_id: str | None = None,
    due=None, reuse_mode: str = "fresh", prev_task_id: str | None = None,
    trigger: str = "manual", payload_extra: dict | None = None,
) -> dict:
    """按模板派发小流程。reuse_mode：fresh 新流程（守卫拦截）/ as_before 与之前一致 /
    adjusted 基于之前调整（prev_task_id 预填）。告警内嵌（alarm_embedded）绕过二选一守卫。

    template_key 以 dyn_ 前缀开头 → 动态填报模板（Excel 解析产物，定义在
    dynamic_template 表），表单由 form_html 承载不走静态 schema，默认两段式审核。
    """
    from . import task_templates

    dynamic_id: str | None = None
    if template_key.startswith("dyn_"):
        from ..modules.dynamic_template import services as dyn_tpl_svc

        dyn = dyn_tpl_svc.get_template_row(db, template_key.removeprefix("dyn_"))
        dynamic_id = dyn.template_id
        tpl = {
            "template_key": template_key,
            "name": dyn.name,
            "scene": "dynamic_fill",
            "review_required": True,
            "related_system": None,
            "form": {"fields": []},  # 动态模板表单由 form_html（iframe）承载
            "title_rule": "{name}",  # 模板名已含语义，不再拼锚点前缀
        }
    else:
        tpl = task_templates.get_template(template_key)
    if not assignee_ids:
        raise errors.validation("请选择承办人")
    anchor = anchor or {}
    if parent_task_id and reuse_mode == "fresh" and trigger != "alarm_embedded":
        children = in_progress_children(db, parent_task_id, anchor)
        if children:
            raise errors.AppError(
                409, "该维度已有进行中的流程，请选择「与之前一致」或「基于调整」",
                {"children": children, "need_choice": True},
            )
    title = task_templates.render_title(tpl, anchor_company=anchor.get("company"), period=period)
    task = create_task(
        db, scene=tpl.get("scene", "generic"), title=title, dispatcher_id=dispatcher_id,
        payload={"template_key": template_key, "form": tpl["form"], "anchor": anchor,
                 **(payload_extra or {})},
        period=period, template_key=template_key, dynamic_template_id=dynamic_id,
        parent_task_id=parent_task_id,
        related_system=tpl.get("related_system"), review_required=bool(tpl.get("review_required")),
        due=due,
        spawn_meta={"trigger": trigger, "anchor": anchor, "prev_task_id": prev_task_id,
                    "reuse_mode": reuse_mode},
    )
    sub = tpl.get("sub_rule", "归属期：{period}").format(
        period=period or "", anchor_company=anchor.get("company") or "",
    )
    ref = {"template_key": template_key, **anchor}
    if dynamic_id:
        ref["dynamic_template_id"] = dynamic_id
    for uid in assignee_ids:
        create_todo(db, task=task, assignee_id=uid, kind="action", title=title, sub=sub, ref=ref)
    db.flush()
    return task_view(db, task)


# ---------------- 任务级操作：催办 / 追加 / 结束 / 看板 ----------------


def remind(db: Session, todo_id: str, operator_id: str) -> dict:
    """催办：仅 pending/returned（待反馈）。天数口径由前端按 created_at 计算。"""
    t = _get(db, todo_id)
    task = _task_of(db, t)
    _require_dispatcher(task, operator_id)
    if t.status not in ("pending", "returned"):
        raise errors.validation(f"当前状态 {t.status} 无需催办")
    t.remind_count = (t.remind_count or 0) + 1
    t.last_reminded_at = _now()
    db.flush()
    _notify_todo(db, t)
    _create_notification(
        db, t, recipient_id=t.assignee_id, actor_id=operator_id,
        category="todo_remind", title=f"{t.title}待处理",
        body=f"任务发起人已催办，请尽快处理。累计催办 {t.remind_count} 次。",
    )
    return todo_view(db, t)


def append_assignees(db: Session, task_id: str, operator_id: str, assignee_ids: list[str]) -> dict:
    """追加承办人：已在范围者单独返回不重复派发（幂等）。"""
    task = db.get(BizTask, task_id)
    if task is None:
        raise errors.not_found("任务不存在")
    _require_dispatcher(task, operator_id)
    if task.status != "open":
        raise errors.validation("任务已结束，不能追加人员")
    existing = set(
        db.scalars(select(BizTodo.assignee_id).where(BizTodo.task_id == task_id)).all()
    )
    created, skipped = [], []
    for uid in assignee_ids:
        if uid in existing:
            skipped.append(uid)
            continue
        todo = create_todo(db, task=task, assignee_id=uid)
        existing.add(uid)
        created.append(todo_view(db, todo))
    db.flush()
    return {"created": created, "skipped": skipped}


def end_task(db: Session, task_id: str, operator_id: str, note: str | None = None, force: bool = False) -> dict:
    """结束任务：全部完成可结束；存在未完成须 force=True（留痕：未完成清单 + 结束说明）。"""
    task = db.get(BizTask, task_id)
    if task is None:
        raise errors.not_found("任务不存在")
    _require_dispatcher(task, operator_id)
    if task.status != "open":
        raise errors.validation("任务已结束")
    todos = db.scalars(select(BizTodo).where(BizTodo.task_id == task_id)).all()
    unfinished = [t for t in todos if t.status not in TERMINAL_STATUSES]
    if unfinished and not force:
        raise errors.validation(
            f"当前待办存在 {len(unfinished)} 个未完成人员，若提前结束请显式确认（force=true）"
        )
    task.status = "ended"
    task.ended_at = _now()
    task.ended_by = operator_id
    task.end_note = note or ""
    db.flush()
    for t in todos:
        _notify_todo(db, t)
    return task_view(db, task)


def task_view(db: Session, task: BizTask) -> dict:
    from .common import dispatcher_name

    return {
        "task_id": task.task_id,
        "parent_task_id": task.parent_task_id,
        "template_key": task.template_key,
        "dynamic_template_id": task.dynamic_template_id,
        "scene": task.scene,
        "title": task.title,
        "period": task.period,
        "status": task.status,
        "related_system": task.related_system,
        "review_required": task.review_required,
        "due": task.due.isoformat() if task.due else None,
        "end_policy": task.end_policy,
        "ended_at": task.ended_at.isoformat() if task.ended_at else None,
        "spawn_meta": task.spawn_meta or {},
        "dispatcher_name": dispatcher_name(db, task.dispatcher_id),
        "created_at": task.created_at.isoformat(),
    }


def row_company(db: Session, t: BizTodo, task: BizTask | None) -> str | None:
    """待办的公司维度（biz_todos 无 company 列，不复制第二事实源）：
    ref.company（风险批次等场景冗余）→ 任务锚点 spawn_meta.anchor.company → 办理人绑定企业。"""
    company = (t.ref or {}).get("company")
    if not company and task is not None:
        company = ((task.spawn_meta or {}).get("anchor") or {}).get("company")
    if not company:
        u = db.get(SysUser, t.assignee_id)
        company = u.company if u is not None else None
    return company


def board(db: Session, task_id: str, operator_id: str) -> dict:
    """任务看板（需求B临时看板/需求A批次看板同源）：状态统计 + 各状态明细。"""
    task = db.get(BizTask, task_id)
    if task is None:
        raise errors.not_found("任务不存在")
    _require_dispatcher(task, operator_id)
    todos = db.scalars(select(BizTodo).where(BizTodo.task_id == task_id)).all()
    groups: dict[str, list[dict]] = {}
    for t in todos:
        groups.setdefault(t.status, []).append({
            "todo_id": t.todo_id,
            "assignee_id": t.assignee_id,
            "assignee_name": _display_name(db, t.assignee_id),
            "company": row_company(db, t, task),
            "title": t.title,
            "status": t.status,
            "remind_count": t.remind_count,
            "last_reminded_at": t.last_reminded_at.isoformat() if t.last_reminded_at else None,
            "submitted_at": t.submitted_at.isoformat() if t.submitted_at else None,
            "returned_at": t.returned_at.isoformat() if t.returned_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            "completion_text": t.completion_text,
            "return_reason": t.return_reason,
            "due": t.due.isoformat() if t.due else None,
            "created_at": t.created_at.isoformat(),  # 下发时间（临时看板明细列）
            "lamp_agg": t.lamp_agg,  # 灯色聚合（看板数据分析用，submit 时物化）
        })
    return {
        **task_view(db, task),
        "stats": {s: len(v) for s, v in groups.items()},
        "groups": groups,
    }


# 首页任务看板页签映射：原始 todo 状态 → 四态页签（遗留 feedback_submitted 归待审批，
# na_closed 视为已了结归已完成；映射在后端统一做，前端不重复维护词表）
BOARD_TAB_OF_STATUS = {
    "submitted": "approve",
    "feedback_submitted": "approve",
    "na_pending": "confirm",
    "pending": "feedback",
    "returned": "feedback",
    "completed": "done",
    "na_closed": "done",
}

# 派发人内核接力单（不涉及确认/反馈复核）不计入首页看板统计：它们是审批动作提醒，
# 不是公司维度交付，混入会虚增各态计数
BOARD_RELAY_KINDS = ("na_confirm", "feedback_review")


def board_summary(db: Session, user_id: str, limit: int = 20) -> list[dict]:
    """首页任务看板聚合：我派发的 open 任务 + 四态页签计数（一次取回，避免前端逐任务 fan-out）。"""
    from .common import dispatcher_name

    tasks = db.scalars(
        select(BizTask)
        .where(BizTask.dispatcher_id == user_id, BizTask.status == "open")
        .order_by(BizTask.created_at.desc())
        .limit(limit)
    ).all()
    if not tasks:
        return []
    stats = {t.task_id: {"approve": 0, "confirm": 0, "feedback": 0, "done": 0} for t in tasks}
    todos = db.scalars(
        select(BizTodo)
        .where(BizTodo.task_id.in_(stats.keys()), BizTodo.kind.notin_(BOARD_RELAY_KINDS))
    ).all()
    totals: dict[str, int] = {tid: 0 for tid in stats}
    last_act: dict[str, list] = {}
    for t in todos:
        s = stats.get(t.task_id)
        if s is None:
            continue
        totals[t.task_id] += 1
        s[BOARD_TAB_OF_STATUS.get(t.status, "feedback")] += 1
        stamps = [ts for ts in (t.submitted_at, t.returned_at, t.completed_at, t.created_at) if ts]
        if stamps:
            last_act.setdefault(t.task_id, []).extend(stamps)
    return [
        {
            "task_id": task.task_id,
            "title": task.title,
            "period": task.period,
            "scene": task.scene,
            "status": task.status,
            "due": task.due.isoformat() if task.due else None,
            "dispatcher_name": dispatcher_name(db, task.dispatcher_id),
            "created_at": task.created_at.isoformat(),
            "total": totals[task.task_id],
            "stats": stats[task.task_id],
            "last_activity_at": max(last_act[task.task_id]).isoformat() if last_act.get(task.task_id) else None,
        }
        for task in tasks
    ]


def in_progress_children(db: Session, parent_task_id: str, anchor: dict | None = None) -> list[dict]:
    """派生守卫：查父任务下未结束的子任务（按 anchor 维度过滤，如同指标）。"""
    rows = db.scalars(
        select(BizTask).where(BizTask.parent_task_id == parent_task_id, BizTask.status == "open")
    ).all()
    out = []
    for r in rows:
        meta = r.spawn_meta or {}
        if anchor and not all(meta.get("anchor", {}).get(k) == v for k, v in anchor.items()):
            continue
        out.append(task_view(db, r))
    return out


# ---------------- 横切：调灯 / 备注 ----------------


def adjust_lamp(
    db: Session, todo_id: str, operator_id: str, new_lamp: str,
    reason: str = "", field_path: str = "overall",
) -> dict:
    """调整亮灯结果：追加流水，永不修改 payload 原灯（展示时以流水最新值覆盖）。"""
    if new_lamp not in ("r", "y", "g"):
        raise errors.validation("灯色仅允许 r/y/g")
    t = _get(db, todo_id)
    task = _task_of(db, t)
    _require_dispatcher(task, operator_id)  # 调灯为审核方（计财部）专属
    if period_locked(task):
        raise errors.validation("已进入下一自然月，期间锁定：仅可查看与添加备注")
    old = t.lamp if field_path == "overall" else None
    log = TaskLampLog(
        todo_id=todo_id, field_path=field_path, old_lamp=old, new_lamp=new_lamp,
        reason=reason, operator_id=operator_id,
    )
    db.add(log)
    if field_path == "overall":
        t.lamp = new_lamp  # 整体灯以最新调整值为准（明细原灯仍走流水追溯）
    db.flush()
    return {"todo_id": todo_id, "field_path": field_path, "new_lamp": new_lamp,
            "reason": reason}


def lamp_log(db: Session, todo_id: str) -> list[dict]:
    rows = db.scalars(
        select(TaskLampLog).where(TaskLampLog.todo_id == todo_id).order_by(TaskLampLog.id)
    ).all()
    return [{
        "field_path": r.field_path, "old_lamp": r.old_lamp, "new_lamp": r.new_lamp,
        "reason": r.reason, "operator_id": r.operator_id, "at": r.created_at.isoformat(),
    } for r in rows]


def add_remark(db: Session, content: str, author_id: str,
               task_id: str | None = None, todo_id: str | None = None) -> dict:
    """添加备注：任务办理期间可用，终态只读。"""
    if not content.strip():
        raise errors.validation("备注内容不能为空")
    if not task_id and not todo_id:
        raise errors.validation("须锚定 task_id 或 todo_id 之一")
    if todo_id:
        todo = _get(db, todo_id)
        task = _task_of(db, todo)
        if author_id not in (todo.assignee_id, task.dispatcher_id):
            raise errors.forbidden("仅办理人或派发人可添加备注")
        if todo.status in TERMINAL_STATUSES:
            raise errors.validation("待办已完成，仅可查看历史备注")
    r = TaskRemark(task_id=task_id, todo_id=todo_id, content=content, author_id=author_id)
    db.add(r)
    db.flush()
    return {"remark_id": r.remark_id, "content": r.content, "author_id": author_id,
            "at": r.created_at.isoformat()}


def delete_remark(db: Session, todo_id: str, remark_id: str, operator_id: str) -> dict:
    """软删除本人备注；任务终态后备注随任务留痕，不再允许删除。"""
    todo = _get(db, todo_id)
    task = _task_of(db, todo)
    if operator_id not in (todo.assignee_id, task.dispatcher_id):
        raise errors.forbidden("仅办理人或派发人可管理备注")
    if todo.status in TERMINAL_STATUSES:
        raise errors.validation("待办已完成，仅可查看历史备注")
    remark = db.scalar(select(TaskRemark).where(
        TaskRemark.remark_id == remark_id,
        TaskRemark.todo_id == todo_id,
    ))
    if remark is None:
        raise errors.not_found("备注不存在")
    if remark.author_id != operator_id:
        raise errors.forbidden("只能删除自己添加的备注")
    remark.is_delete = True
    db.flush()
    return {"remark_id": remark_id, "deleted": True}


def list_remarks(db: Session, task_id: str | None = None, todo_id: str | None = None) -> list[dict]:
    q = select(TaskRemark).order_by(TaskRemark.created_at)
    if todo_id:
        q = q.where(TaskRemark.todo_id == todo_id)
    elif task_id:
        q = q.where(TaskRemark.task_id == task_id)
    else:
        raise errors.validation("须提供 task_id 或 todo_id 之一")
    return [{
        "remark_id": r.remark_id, "content": r.content, "author_id": r.author_id,
        "at": r.created_at.isoformat(),
    } for r in db.scalars(q).all()]


# ---------------- 期间门控（自然月规则，计算式不落库） ----------------


def period_locked(task: BizTask, today: datetime | None = None) -> bool:
    """进入下一自然月 → 期间锁定：除查看/添加备注外全部按钮隐藏（2026-01-19 口径）。"""
    if not task.period:
        return False
    today = today or _now()
    cur = f"{today.year:04d}-{today.month:02d}"
    return task.period < cur


def derive_overdue(t: BizTodo, task: BizTask, today: datetime | None = None) -> bool:
    """过自然月未填报 → 未填报（入口隐藏，2026-01-15 口径）；派生态不落库。"""
    return t.status in ("pending", "returned") and period_locked(task, today)


# ---------------- 内部工具 ----------------


def _count_lamps(payload: dict) -> dict:
    """按 payload.items[].lamp 统计灯色聚合（无 items 结构则空）。"""
    agg = {"r": 0, "y": 0, "g": 0}
    for it in (payload or {}).get("items", []) or []:
        lamp = it.get("lamp")
        if lamp in agg:
            agg[lamp] += 1
    return agg


def _display_name(db: Session, user_id: str) -> str:
    u = db.get(SysUser, user_id)
    if u is not None:
        return u.display_name
    # 主库无行（SCMS 透传真实用户）→ 业务库 system_users 取昵称
    from . import identity as identity_svc

    return identity_svc.user_names([user_id]).get(str(user_id), str(user_id))
