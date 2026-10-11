"""对话附件上传/下载（问答链路）：POST multipart 上传即入库+解析，GET 鉴权下载本体。

上传与会话解耦（开屏首问先传后建会话），发消息时经 sessions.chat 按 id 绑定。
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core import errors

from app.persistence.models import SysUser
from app.persistence.session import get_db
from app.platform.api.deps import current_user

from ...services import chat_files

router = APIRouter(prefix="/attachments", tags=["attachments"])
logger = logging.getLogger(__name__)


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """上传一个问答附件：类型/大小校验 → MinIO（未配置回退本地）→ 同步解析 → 元数据入库。

    返回契约 AttachmentMeta；解析失败不阻断上传（status=failed，问答链路按文件名降级提及）。
    """
    raw = await file.read()
    if not raw:
        raise errors.validation("文件内容为空")
    a = chat_files.save_upload(db, content=raw, file_name=file.filename or "未命名", user_id=user.user_id)
    return chat_files.attachment_view(a)


@router.get("/{attachment_id}/download")
def download(
    attachment_id: str,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """下载附件本体（鉴权读取，filename* 携带原始文件名）。"""
    a = chat_files.get_owned(db, attachment_id, user.user_id)
    content = chat_files.read_bytes(a)
    return Response(
        content=content,
        media_type=a.mime or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(a.file_name)}"},
    )
