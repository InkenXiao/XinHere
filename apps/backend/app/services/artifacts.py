"""任务交付物（附件）：对象存储（MinIO 优先，未配置回退本地）+ task_artifacts 元数据（P3-1 唯一全新基础设施）。
扩展名白名单 + 大小上限 + sha256 摘要（键内去重）；下载走鉴权端点而非静态挂载。
storage_key 由服务端生成（todo_id/摘要_文件名），杜绝路径穿越。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import errors
from ..persistence.models import BizTask, BizTodo, SysUser, TaskArtifact
from . import storage

# 允许的扩展名：文档/表格/演示/图片/压缩包/文本
ALLOWED_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".rar", ".7z",
}
DEFAULT_MAX_MB = 20
_NAME_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def max_mb() -> int:
    from ..core.config import settings

    return settings.artifact_max_mb or DEFAULT_MAX_MB


def _can_access(db: Session, t: BizTodo | None, task: BizTask | None, user_id: str) -> bool:
    if t and t.assignee_id == user_id:
        return True
    return bool(task and task.dispatcher_id == user_id)


def save_upload(db: Session, *, content: bytes, file_name: str, todo_id: str,
                uploader_id: str) -> dict:
    t = db.get(BizTodo, todo_id)
    if t is None:
        raise errors.not_found("待办不存在")
    task = db.get(BizTask, t.task_id)
    if not _can_access(db, t, task, uploader_id):
        raise errors.forbidden("无权向该待办上传交付物")
    if not content:
        raise errors.validation("空文件")
    safe = _NAME_BAD.sub("_", file_name).strip() or "未命名"
    ext = Path(safe).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise errors.validation(f"不支持的文件类型：{ext or '（无扩展名）'}")
    limit = max_mb() * 1024 * 1024
    if len(content) > limit:
        raise errors.validation(f"文件超过 {max_mb()}MB 上限")
    sha = hashlib.sha256(content).hexdigest()
    storage_key = f"{todo_id}/{sha[:12]}_{safe}"
    # 对象存储（MinIO）或本地磁盘元组键幂等去重写；重复内容不重传
    if not storage.exists(storage_key):
        storage.put(storage_key, content, content_type=ext.lstrip(".") or "application/octet-stream")
    a = TaskArtifact(
        todo_id=todo_id, task_id=t.task_id, file_name=safe, storage_key=storage_key,
        mime_type=ext.lstrip("."), file_size=len(content), sha256=sha, uploaded_by=uploader_id,
    )
    db.add(a)
    db.flush()
    return artifact_view(a)


def artifact_view(a: TaskArtifact) -> dict:
    return {
        "artifact_id": a.artifact_id,
        "todo_id": a.todo_id,
        "task_id": a.task_id,
        "file_name": a.file_name,
        "mime_type": a.mime_type,
        "file_size": a.file_size,
        "kb_included": bool(a.kb_included),
        "uploaded_by": a.uploaded_by,
        "created_at": a.created_at.isoformat(),
    }


def _get_checked(db: Session, artifact_id: str, user_id: str) -> TaskArtifact:
    a = db.get(TaskArtifact, artifact_id)
    if a is None:
        raise errors.not_found("交付物不存在")
    t = db.get(BizTodo, a.todo_id)
    task = db.get(BizTask, a.task_id) if a.task_id else None
    if not _can_access(db, t, task, user_id):
        raise errors.forbidden("无权访问该交付物")
    return a


def list_by_todo(db: Session, todo_id: str, user_id: str) -> list[dict]:
    t = db.get(BizTodo, todo_id)
    if t is None:
        raise errors.not_found("待办不存在")
    task = db.get(BizTask, t.task_id)
    if not _can_access(db, t, task, user_id):
        raise errors.forbidden("无权查看该待办交付物")
    rows = db.scalars(
        select(TaskArtifact).where(TaskArtifact.todo_id == todo_id).order_by(TaskArtifact.created_at)
    ).all()
    return [artifact_view(a) for a in rows]


def list_by_task(db: Session, task_id: str, user_id: str) -> list[dict]:
    """任务级交付物：该任务全部待办的附件（派发人专属，首页任务看板「查看交付物」用）。"""
    from .todo import row_company

    task = db.get(BizTask, task_id)
    if task is None:
        raise errors.not_found("任务不存在")
    if task.dispatcher_id != user_id:
        raise errors.forbidden("仅派发人可查看任务交付物")
    rows = db.execute(
        select(TaskArtifact, BizTodo)
        .join(BizTodo, BizTodo.todo_id == TaskArtifact.todo_id)
        .where(BizTodo.task_id == task_id)
        .order_by(TaskArtifact.created_at.desc())
    ).all()
    items: list[dict] = []
    for a, t in rows:
        company = row_company(db, t, task)
        assignee = db.get(SysUser, t.assignee_id)
        items.append({
            "artifact_id": a.artifact_id,
            "todo_id": a.todo_id,
            "company": company,
            "assignee_name": assignee.display_name if assignee is not None else t.assignee_id,
            "file_name": a.file_name,
            "size": int(a.file_size or 0),
            "created_at": a.created_at.isoformat(),
        })
    return items


def get_download(db: Session, artifact_id: str, user_id: str) -> tuple[bytes, TaskArtifact]:
    a = _get_checked(db, artifact_id, user_id)
    return storage.read(a.storage_key), a


def set_kb_included(db: Session, artifact_id: str, included: bool, user_id: str) -> dict:
    """知识库纳入状态标记（需求 B 审核弹窗展示；实际入库由知识库侧流程承接）。"""
    a = _get_checked(db, artifact_id, user_id)
    a.kb_included = included
    db.flush()
    return artifact_view(a)
