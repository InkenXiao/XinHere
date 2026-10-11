"""历史任务 · task_records 统一树（Xin语对话记录 / Xin台执行记录 / 用户文件夹）

- GET /tasks?scope=chat：惰性同步 platform_sessions → chat 记录（标题取首条用户消息，改名后不再覆盖）
- GET /tasks?scope=exec：Xin台 AI 能力执行记录（POST /tasks/exec 落库、PATCH 更新状态）
- 文件夹：POST /tasks/folders；重命名 PATCH /tasks/{id}
- 拖拽层级/排序：POST /tasks/reorder（目标父级 + 完整顺序）
- 逻辑删除：DELETE /tasks/{id}（文件夹级联软删子项；chat 记录删除后同步不复活）
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import errors
from ...persistence.models import (
    PlatformSession,
    PlatformSessionEvent,
    SysUser,
    TaskRecord,
)
from ...persistence.session import get_db
from .deps import current_user

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _ts(dt: datetime | None) -> int:
    return int(dt.timestamp()) if dt else 0


def _first_user_message(db: Session, sid) -> str:
    row = db.scalars(
        select(PlatformSessionEvent.data)
        .where(PlatformSessionEvent.session_id == sid,
               PlatformSessionEvent.type == "user/message")
        .order_by(PlatformSessionEvent.seq)
        .limit(1)
    ).first()
    return (row.get("content", "") or "").strip() if isinstance(row, dict) else ""


def _derive_title(db: Session, s: PlatformSession) -> str:
    """记录标题：会话标题 > 首条用户消息 > 新对话。"""
    if s.title:
        return s.title[:60]
    return _first_user_message(db, s.session_id)[:60] or "新对话"


def _sync_chat_records(db: Session, user: SysUser) -> None:
    """惰性同步 chat 记录：缺则建（含已删记录判定，避免删除后复活）。"""
    sessions = db.scalars(
        select(PlatformSession).where(PlatformSession.user_id == user.user_id)
    ).all()
    if not sessions:
        return
    refs = [str(s.session_id) for s in sessions]
    records = db.scalars(
        select(TaskRecord).where(
            TaskRecord.user_id == user.user_id,
            TaskRecord.kind == "chat",
            TaskRecord.ref_id.in_(refs),
        )
    ).all()
    by_ref = {r.ref_id: r for r in records}
    for s in sessions:
        ref = str(s.session_id)
        rec = by_ref.get(ref)
        if rec is None:
            title = _derive_title(db, s)
            db.add(TaskRecord(
                user_id=user.user_id, kind="chat", scope="chat",
                title=title, ref_id=ref,
                sort=_ts(s.updated_at),
                detail={"titled": title != "新对话"},
            ))
        elif not rec.is_delete and not (rec.detail or {}).get("titled"):
            # 创建时还没有消息：补派生标题；用户改名（titled=True）后不再覆盖
            title = _derive_title(db, s)
            if title != "新对话" and title != rec.title:
                rec.title = title
                rec.detail = {**(rec.detail or {}), "titled": True}


def _view(r: TaskRecord) -> dict:
    return {
        "id": r.id,
        "kind": r.kind,  # chat / exec / folder
        "scope": r.scope,  # chat / exec
        "title": r.title,
        "status": r.status,
        "ref_id": r.ref_id,
        "detail": r.detail or {},
        "parent_id": r.parent_id,
        "sort": r.sort,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _get_record(db: Session, user: SysUser, record_id: str) -> TaskRecord:
    r = db.get(TaskRecord, record_id)
    if r is None or r.user_id != user.user_id or r.is_delete:
        raise errors.not_found("任务不存在")
    return r


def _reaches(db: Session, start_id: str | None, target_id: str) -> bool:
    """start 向上寻父链是否经过 target（拖拽防环）。"""
    cur, seen = start_id, set()
    while cur:
        if cur == target_id or cur in seen:
            return True
        seen.add(cur)
        row = db.get(TaskRecord, cur)
        if row is None:
            return False
        cur = row.parent_id
    return False


@router.get("")
def list_tasks(scope: str = Query("chat", pattern="^(chat|exec)$"),
               user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    if scope == "chat":
        _sync_chat_records(db, user)
        db.flush()
    rows = db.scalars(
        select(TaskRecord).where(
            TaskRecord.user_id == user.user_id,
            TaskRecord.scope == scope,
            TaskRecord.is_delete.is_(False),
        ).order_by(TaskRecord.sort.desc(), TaskRecord.created_at.desc())
    ).all()
    return {"items": [_view(r) for r in rows]}


class FolderIn(BaseModel):
    title: str = "新建文件夹"
    scope: str = "chat"  # chat / exec
    parent_id: str | None = None


@router.post("/folders", status_code=201)
def create_folder(body: FolderIn, user: SysUser = Depends(current_user),
                  db: Session = Depends(get_db)):
    if body.scope not in ("chat", "exec"):
        raise errors.validation("scope 须为 chat / exec")
    if body.parent_id:
        parent = _get_record(db, user, body.parent_id)
        if parent.kind != "folder":
            raise errors.validation("只能建在文件夹内")
    folder = TaskRecord(
        user_id=user.user_id, kind="folder", scope=body.scope,
        title=body.title.strip()[:60] or "新建文件夹",
        parent_id=body.parent_id,
        sort=_ts(datetime.now(timezone.utc)),
    )
    db.add(folder)
    db.flush()
    return _view(folder)


class ExecIn(BaseModel):
    title: str
    status: str = "running"  # running / success / failed / stopped
    ref_id: str | None = None  # 来源卡片 key（如 minutes）
    detail: dict = {}


@router.post("/exec", status_code=201)
def create_exec(body: ExecIn, user: SysUser = Depends(current_user),
                db: Session = Depends(get_db)):
    rec = TaskRecord(
        user_id=user.user_id, kind="exec", scope="exec",
        title=body.title.strip()[:120] or "执行记录",
        status=body.status, ref_id=body.ref_id, detail=body.detail,
        sort=_ts(datetime.now(timezone.utc)),
    )
    db.add(rec)
    db.flush()
    return _view(rec)


class TaskPatch(BaseModel):
    title: str | None = None
    status: str | None = None
    detail: dict | None = None


@router.patch("/{record_id}")
def update_task(record_id: str, body: TaskPatch,
                user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    r = _get_record(db, user, record_id)
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise errors.validation("名称不能为空")
        r.title = title[:120]
        if r.kind == "chat":
            # 用户改名后，chat 同步不再用派生标题覆盖
            r.detail = {**(r.detail or {}), "titled": True}
    if body.status is not None:
        r.status = body.status
    if body.detail is not None:
        r.detail = {**(r.detail or {}), **body.detail}
    db.flush()
    return _view(r)


class ReorderIn(BaseModel):
    parent_id: str | None = None  # 目标父级（null=顶层；文件夹 id=移入）
    ids: list[str] = []  # 该父级下完整顺序（首位在前）


@router.post("/reorder")
def reorder(body: ReorderIn, user: SysUser = Depends(current_user),
            db: Session = Depends(get_db)):
    if not body.ids:
        return {"ok": True}
    parent = None
    if body.parent_id:
        parent = _get_record(db, user, body.parent_id)
        if parent.kind != "folder":
            raise errors.validation("只能移入文件夹")
    n = len(body.ids)
    for idx, rid in enumerate(body.ids):
        r = _get_record(db, user, rid)
        if parent is not None:
            if r.kind == "folder" and _reaches(db, parent.id, r.id):
                raise errors.validation("不能把文件夹移入它自己内部")
            if r.scope != parent.scope:
                raise errors.validation("不能跨列表移动")
        r.parent_id = body.parent_id
        r.sort = n - idx  # 前位在前 → sort 降序展示（大者在前）
    db.flush()
    return {"ok": True}


def _soft_delete(db: Session, user: SysUser, r: TaskRecord) -> None:
    r.is_delete = True
    if r.kind != "folder":
        return
    children = db.scalars(
        select(TaskRecord).where(
            TaskRecord.user_id == user.user_id,
            TaskRecord.parent_id == r.id,
            TaskRecord.is_delete.is_(False),
        )
    ).all()
    for c in children:
        _soft_delete(db, user, c)


@router.delete("/{record_id}")
def delete_task(record_id: str, user: SysUser = Depends(current_user),
                db: Session = Depends(get_db)):
    r = _get_record(db, user, record_id)
    _soft_delete(db, user, r)
    db.flush()
    return {"ok": True}


# ---------------- 任务内核端点（scms 移植；与上方记录树端点共用 /tasks 前缀） ----------------

from ...core import errors  # noqa: E402
from ...modules.dynamic_template import services as dyn_tpl_svc  # noqa: E402
from ...services import artifacts as artifacts_svc  # noqa: E402
from ...services import task_templates  # noqa: E402
from ...services import todo as todo_svc  # noqa: E402
from .deps import require_hq  # noqa: E402


@router.get("/templates")
def list_templates(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """任务模板目录（小流程清单）：前端派发弹窗与表单渲染的依据；含动态填报模板（dyn_ 前缀）。"""
    return {"items": dyn_tpl_svc.merge_dynamic_into_list(db, task_templates.list_template_views())}


class SpawnIn(BaseModel):
    template_key: str
    assignee_ids: list[str] = Field(default_factory=list)
    period: str | None = None
    anchor: dict | None = None
    parent_task_id: str | None = None
    due: str | None = None
    reuse_mode: str = "fresh"
    prev_task_id: str | None = None


@router.post("")
def spawn_task(body: SpawnIn, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """按模板派发小流程（风险应对方案/应对项等）。fresh 且同锚点有进行中子流程 →
    409 + children 明细（need_choice），前端弹出「与之前一致 / 基于调整」二选一。
    dyn_ 前缀 → 动态填报模板（模板定义在 dynamic_template 表，无静态 creator_roles）。"""
    if not body.template_key.startswith("dyn_"):
        tpl = task_templates.get_template(body.template_key)
        roles = tpl.get("creator_roles") or []
        if roles and user.role not in roles and user.role != "hq_finance":
            raise errors.forbidden("当前角色不可发起该流程")
    due = datetime.fromisoformat(body.due) if body.due else None
    result = todo_svc.spawn_task(
        db, template_key=body.template_key, dispatcher_id=user.user_id,
        assignee_ids=body.assignee_ids, period=body.period, anchor=body.anchor,
        parent_task_id=body.parent_task_id, due=due, reuse_mode=body.reuse_mode,
        prev_task_id=body.prev_task_id,
    )
    # 待办创建阶段的 SSE 可能早于请求级事务提交，接收端立即刷新会查不到新数据。
    # 此处先提交，再补发一次变更通知，保证派发人与办理人都能立即看到任务。
    db.commit()
    todos = db.scalars(select(BizTodo).where(BizTodo.task_id == result["task_id"])).all()
    for todo in todos:
        todo_svc._notify_todo(db, todo)
    return result


@router.get("/open")
def list_open_tasks(only_open: bool = True, user: SysUser = Depends(current_user),
               db: Session = Depends(get_db)):
    """我派发的任务列表（看板入口；board 为派发人专属，故此处只列我派发的）。"""
    q = select(BizTask).where(BizTask.dispatcher_id == user.user_id)
    if only_open:
        q = q.where(BizTask.status == "open")
    q = q.order_by(BizTask.created_at.desc()).limit(50)
    rows = db.scalars(q).all()
    return {"items": [todo_svc.task_view(db, t) for t in rows]}


@router.get("/board-summary")
def board_summary(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """首页任务看板聚合：我派发的 open 任务 + 四态页签计数（待审批/待确认/待反馈/已完成）。"""
    return {"items": todo_svc.board_summary(db, user.user_id)}


@router.get("/{task_id}/board")
def board(task_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """任务看板（临时看板/概览）：状态统计 + 各状态人员明细。"""
    return todo_svc.board(db, task_id, user.user_id)


@router.get("/{task_id}/artifacts")
def task_artifacts(task_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """任务级交付物列表（该任务全部待办的附件，仅派发人）。"""
    return {"items": artifacts_svc.list_by_task(db, task_id, user.user_id)}


@router.get("/{task_id}/analytics")
def analytics(task_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """动态信息填报任务的数据分析（仅派发人）。"""
    return dyn_tpl_svc.task_analytics(db, task_id, user.user_id)


class EndIn(BaseModel):
    note: str | None = None
    force: bool = False


@router.post("/{task_id}/end")
def end(task_id: str, body: EndIn, user: SysUser = Depends(current_user),
        db: Session = Depends(get_db)):
    return todo_svc.end_task(db, task_id, user.user_id, body.note, body.force)


class AppendIn(BaseModel):
    assignee_ids: list[str]


@router.post("/{task_id}/append")
def append(task_id: str, body: AppendIn, user: SysUser = Depends(current_user),
           db: Session = Depends(get_db)):
    """追加承办人：已在范围内者返回 skipped 不重复派发。"""
    return todo_svc.append_assignees(db, task_id, user.user_id, body.assignee_ids)


@router.get("/{task_id}/children")
def children(task_id: str, anchor_company: str | None = None, anchor_indicator: str | None = None,
             user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """派生守卫：父任务下进行中的子任务（可按企业/指标锚点过滤，用于「与之前一致/基于调整」二选一）。"""
    anchor: dict | None = None
    if anchor_company or anchor_indicator:
        anchor = {}
        if anchor_company:
            anchor["company"] = anchor_company
        if anchor_indicator:
            anchor["indicator"] = anchor_indicator
    return {"items": todo_svc.in_progress_children(db, task_id, anchor)}


class RemarkIn(BaseModel):
    content: str


@router.post("/{task_id}/remarks")
def add_task_remark(task_id: str, body: RemarkIn, user: SysUser = Depends(current_user),
                    db: Session = Depends(get_db)):
    return todo_svc.add_remark(db, body.content, user.user_id, task_id=task_id)


@router.get("/{task_id}/remarks")
def list_task_remarks(task_id: str, user: SysUser = Depends(current_user),
                      db: Session = Depends(get_db)):
    return {"items": todo_svc.list_remarks(db, task_id=task_id)}
