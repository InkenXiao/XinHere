"""用户个人模型偏好 (sys_user_model_prefs)

按模型通道 (main/small/coder/vision) 保存用户级覆盖，空字段/0 回退 .docker.env 系统默认；
未设置覆盖的通道完全走系统默认。llm.build_model 经 resolve_override 消费。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import errors
from ..core.config import settings
from ..persistence.models import SysUserModelPref

CHANNELS = ("main", "small", "coder", "vision")


def _env_default(channel: str) -> dict:
    """通道的系统默认（.docker.env；SMALL/CODER/VISION 未配置时回退主推理模型）。"""
    if channel == "main":
        return {"api_url": settings.main_api_url, "model": settings.main_model,
                "max_tokens": settings.main_max_tokens}
    if channel == "small":
        return {"api_url": settings.small_api_url or settings.main_api_url,
                "model": settings.small_model or settings.main_model,
                "max_tokens": settings.small_max_tokens or settings.main_max_tokens}
    if channel == "coder":
        return {"api_url": settings.coder_api_url or settings.main_api_url,
                "model": settings.coder_model or settings.main_model,
                "max_tokens": settings.coder_max_tokens or settings.main_max_tokens}
    if channel == "vision":
        return {"api_url": settings.vision_api_url or settings.main_api_url,
                "model": settings.vision_model or settings.main_model,
                "max_tokens": settings.vision_max_tokens or settings.main_max_tokens}
    raise errors.validation(f"未知模型通道：{channel}")


def _row(db: Session, user_id: str, channel: str) -> SysUserModelPref | None:
    return db.scalar(select(SysUserModelPref).where(
        SysUserModelPref.user_id == user_id,
        SysUserModelPref.channel == channel,
        SysUserModelPref.is_delete.is_(False),
    ))


def resolve_override(user_id: str | None, channel: str) -> dict | None:
    """LLM 客户端用：返回该用户在通道上的非空覆盖字段；无覆盖/读取失败返回 None（静默回退默认）。"""
    if not user_id or channel not in CHANNELS:
        return None
    try:
        from ..persistence.session import SessionLocal

        with SessionLocal() as db:
            row = _row(db, user_id, channel)
            if row is None:
                return None
            ov: dict = {}
            if row.api_url.strip():
                ov["api_url"] = row.api_url.strip()
            if row.api_key.strip():
                ov["api_key"] = row.api_key.strip()
            if row.model.strip():
                ov["model"] = row.model.strip()
            if row.max_tokens:
                ov["max_tokens"] = row.max_tokens
            return ov or None
    except Exception:  # noqa: BLE001 - 偏好读取失败不影响主流程
        return None


def _ov_view(row: SysUserModelPref) -> dict:
    return {"api_url": row.api_url, "api_key_set": bool(row.api_key.strip()),
            "model": row.model, "max_tokens": row.max_tokens}


def effective(db: Session, user_id: str) -> dict:
    """全部通道的系统默认 + 该用户覆盖（api_key 不回显明文，只回显是否已设置）。"""
    channels = {}
    for ch in CHANNELS:
        d = _env_default(ch)
        channels[ch] = {
            "api_url": d["api_url"], "model": d["model"], "max_tokens": d["max_tokens"],
            "override": None,
        }
    for row in db.scalars(select(SysUserModelPref).where(
            SysUserModelPref.user_id == user_id, SysUserModelPref.is_delete.is_(False))).all():
        if row.channel in channels:
            channels[row.channel]["override"] = _ov_view(row)
    return {"channels": channels}


def upsert(db: Session, user_id: str, channel: str, *, api_url: str, api_key: str,
           model: str, max_tokens: int) -> dict:
    """保存通道覆盖；传空值即清除对应字段（回退系统默认），全空等价于删除。"""
    if channel not in CHANNELS:
        raise errors.validation(f"未知模型通道：{channel}")
    row = _row(db, user_id, channel)
    vals = dict(api_url=api_url.strip()[:512], api_key=api_key.strip()[:256],
                model=model.strip()[:128], max_tokens=max(int(max_tokens or 0), 0))
    if row is None:
        row = SysUserModelPref(user_id=user_id, channel=channel, **vals)
        db.add(row)
    else:
        for k, v in vals.items():
            setattr(row, k, v)
    db.commit()
    return _ov_view(row)


def remove(db: Session, user_id: str, channel: str) -> None:
    if channel not in CHANNELS:
        raise errors.validation(f"未知模型通道：{channel}")
    row = _row(db, user_id, channel)
    if row is not None:
        row.is_delete = True
        db.commit()
