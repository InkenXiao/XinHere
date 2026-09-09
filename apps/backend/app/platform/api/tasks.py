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
from pydantic import BaseModel
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
