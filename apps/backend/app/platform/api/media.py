"""附件上传与语音转写 (对话页 WorkBuddy 工具条)"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ...core import errors
from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import files as files_svc
from ...services import xuanpu as xuanpu_svc
from .deps import current_user

router = APIRouter(tags=["media"])


def _identity(db: Session, user: SysUser) -> str:
    return (user.display_name or user.username) if user else ""


@router.post("/files/upload")
async def upload_file(file: UploadFile, user: SysUser = Depends(current_user)):
    try:
        return await files_svc.save_upload(user.user_id, file)
    except ValueError as exc:
        raise errors.validation(str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"上传失败：{exc}") from exc


@router.post("/audio/transcribe")
async def transcribe_audio(file: UploadFile,
                           user: SysUser = Depends(current_user),
                           db: Session = Depends(get_db)):
    token = xuanpu_svc.resolve_token(db, user.user_id)
    if not token:
        raise HTTPException(status_code=502, detail="语音转写服务暂不可用，请重新登录后重试")
    raw = await file.read()
    if not raw:
        raise errors.validation("音频内容为空")
    try:
        return await xuanpu_svc.audio_transcribe(
            _identity(db, user), token,
            file.filename or "clip.webm", file.content_type or "audio/webm", raw,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"转写失败：{str(exc)[:120]}") from exc
