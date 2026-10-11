"""外部权限系统判权钩子（驾驶舱/Xin台卡片显隐的唯一接入点）。

设计：配置表（cockpit_cards.perm_key）只存权限资源标识，"谁能看"由外部权限系统判定。
- PERM_CHECK_URL 未配置（未接入）：perm_key 卡全部放行 —— 否则 seed 的判权卡会被整批隐藏；
- 已配置：对 perm_key 批量判权（POST {user_id, keys} → {keys: [...]}），
  接口异常/超时/响应异常 → fail-closed（判权失败的卡隐藏，安全侧）。
"""
from __future__ import annotations

import logging

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)


def visible_perm_keys(user_id: str, keys: list[str]) -> set[str]:
    """返回当前用户可见的 perm_key 集合。keys 为空直接返回空集（调用方无需判空）。"""
    if not keys:
        return set()
    if not settings.perm_check_url:
        return set(keys)  # 权限系统未接入：放行（接入配置后自动切换为真实判权）
    try:
        r = httpx.post(
            settings.perm_check_url,
            json={"user_id": user_id, "keys": keys},
            timeout=settings.perm_check_timeout_seconds,
        )
        r.raise_for_status()
        data = r.json()
        allowed = data.get("keys") if isinstance(data, dict) else None
        return {str(k) for k in (allowed or [])}
    except Exception:
        # fail-closed：权限系统不可达时隐藏判权卡（可用性让位于安全侧），不影响无 perm_key 的卡
        logger.warning("权限系统判权失败，fail-closed 隐藏 %d 张卡 user=%s", len(keys), user_id, exc_info=True)
        return set()
