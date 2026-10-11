"""声纹识别 API：生物信息采集告知同意存证（PIPL 单独同意）+ 采样进度查询。

- GET  /consents/biometric  查询当前用户同意状态
- POST /consents/biometric  写入同意存证（协议版本从 GET 响应回传，防错版本）
- GET  /voiceprint/status   无感采样进度（enrolled / sample_seconds / min_seconds）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel


from app.core.errors import validation

from ...platform.api.deps import current_user
from ...persistence.models import SysUser
from ...services import voiceprint as voiceprint_svc
from ...core.config import settings

router = APIRouter(prefix="", tags=["voiceprint"])
logger = logging.getLogger(__name__)


class BiometricConsentBody(BaseModel):
    protocol_version: str


@router.get("/consents/biometric")
def get_biometric_consent(user: SysUser = Depends(current_user)):
    """当前用户声纹采集同意状态（未同意时 consent_given=false）。"""
    row = voiceprint_svc.consent_record(user.user_id)
    return {
        "consent_given": row is not None,
        "protocol_version": settings.biometric_protocol_version,
        "agreed_at": row.agreed_at.isoformat() if row is not None else None,
    }


@router.post("/consents/biometric")
def post_biometric_consent(body: BiometricConsentBody, user: SysUser = Depends(current_user)):
    """写入同意存证（一人一条，重复同意覆盖版本与时间；版本不匹配拒绝存证）。"""
    if body.protocol_version != settings.biometric_protocol_version:
        raise validation(
            f"协议版本不匹配：当前为 {settings.biometric_protocol_version}",
            details={"expected": settings.biometric_protocol_version, "got": body.protocol_version},
        )
    voiceprint_svc.record_consent(user.user_id, body.protocol_version)
    logger.info("生物信息采集同意存证：user=%s version=%s", user.user_id, body.protocol_version)
    return {"consent_given": True, "protocol_version": body.protocol_version}


@router.get("/voiceprint/status")
def voiceprint_status(user: SysUser = Depends(current_user)):
    """声纹采样进度（前端 Toast 提示与调试用）。"""
    return voiceprint_svc.status(user.user_id)


@router.delete("/voiceprint")
def delete_voiceprint(user: SysUser = Depends(current_user)):
    """删除当前用户全部声纹数据（采样与注册档案，PIPL 删除权）。"""
    result = voiceprint_svc.delete_voiceprint(user.user_id)
    logger.info("声纹删除请求：user=%s result=%s", user.user_id, result)
    return result


@router.delete("/consents/biometric")
def revoke_biometric_consent(user: SysUser = Depends(current_user)):
    """撤回生物信息采集同意，并连带删除全部声纹数据（PIPL 撤回权）。"""
    result = voiceprint_svc.revoke_consent(user.user_id)
    logger.info("生物信息采集同意撤回：user=%s result=%s", user.user_id, result)
    return result
