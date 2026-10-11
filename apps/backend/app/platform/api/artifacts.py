from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session



from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import artifacts as artifacts_svc
from .deps import current_user

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.post("")
async def upload(file: UploadFile = File(...), todo_id: str = Form(...),
                 user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """交付物上传（multipart）：扩展名白名单 + 大小上限，落盘 + task_artifacts 元数据。"""
    content = await file.read()
    return artifacts_svc.save_upload(
        db, content=content, file_name=file.filename or "未命名",
        todo_id=todo_id, uploader_id=user.user_id,
    )


@router.get("")
def list_artifacts(todo_id: str = Query(...), user: SysUser = Depends(current_user),
                   db: Session = Depends(get_db)):
    return {"items": artifacts_svc.list_by_todo(db, todo_id, user.user_id)}


@router.get("/{artifact_id}/download")
def download(artifact_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    content, a = artifacts_svc.get_download(db, artifact_id, user.user_id)
    return Response(content=content, media_type="application/octet-stream")


class KbIncludedIn(BaseModel):
    included: bool


@router.post("/{artifact_id}/kb-included")
def kb_included(artifact_id: str, body: KbIncludedIn, user: SysUser = Depends(current_user),
                db: Session = Depends(get_db)):
    """知识库纳入状态标记（审核弹窗展示）。"""
    return artifacts_svc.set_kb_included(db, artifact_id, body.included, user.user_id)
