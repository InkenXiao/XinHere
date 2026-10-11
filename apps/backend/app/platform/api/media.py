"""附件上传与语音转写 (对话页 WorkBuddy 工具条)。

ASR_PROVIDER=xuanpu（默认）：玄圃 pro-cowork 转写；
ASR_PROVIDER=gateway：LLM 网关同源 ASR + 声纹无感采样（scms 会议管线）。
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

import logging

from ...core import errors
from ...core.config import settings
from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import files as files_svc
from ...services import xuanpu as xuanpu_svc
from .deps import current_user

router = APIRouter(tags=["media"])

logger = logging.getLogger(__name__)


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


# 网关 ASR 单段上限：WAV 16kHz 16-bit 单声道 ≈ 32KB/s × 5 分钟
MAX_CLIP_BYTES = 32000 * 60 * 5


@router.post("/audio/transcribe")
async def transcribe_audio(file: UploadFile,
                           user: SysUser = Depends(current_user),
                           db: Session = Depends(get_db)):
    """语音转写：ASR_PROVIDER=xuanpu（默认，玄圃 pro-cowork，WorkBuddy 链路）；
    gateway（LLM 网关同源 ASR，scms 会议管线）转写并附带声纹无感采样结果。"""
    raw = await file.read()
    if not raw:
        raise errors.validation("音频内容为空")

    if settings.asr_provider == "gateway":
        import asyncio

        from ...services import asr as asr_svc
        from ...services import voiceprint as voiceprint_svc

        if len(raw) > MAX_CLIP_BYTES:
            raise errors.validation("录音片段过长，请分段录制")
        cleaned, raw_text = await asr_svc.transcribe_audio_chunk(raw)
        if not cleaned:
            logger.info("语音转写无有效内容：user=%s raw=%s", user.user_id, (raw_text or "")[:80])
            return {"ok": False, "text": "", "voiceprint_enrolled": False}
        just_enrolled = False
        acc: dict = {}
        try:
            acc = await asyncio.to_thread(voiceprint_svc.accumulate_sample, user.user_id, raw)
            just_enrolled = bool(acc.get("just_enrolled"))
        except Exception:
            logger.warning("声纹采样异常（不影响听写）user=%s", user.user_id, exc_info=True)
        return {
            "ok": True,
            "text": cleaned,
            "voiceprint_enrolled": just_enrolled,
            "voiceprint_active": bool(acc.get("enrolled")),
            "voiceprint_seconds": float(acc.get("sample_seconds") or 0.0),
            "voiceprint_min_seconds": float(settings.voiceprint_min_enroll_seconds),
            "voiceprint_consented": bool(acc.get("consented", True)),
        }

    token = xuanpu_svc.resolve_token(db, user.user_id)
    if not token:
        raise HTTPException(status_code=502, detail="语音转写服务暂不可用，请重新登录后重试")
    try:
        return await xuanpu_svc.audio_transcribe(
            _identity(db, user), token,
            file.filename or "clip.webm", file.content_type or "audio/webm", raw,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"转写失败：{str(exc)[:120]}") from exc
