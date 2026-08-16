from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any

# 脱敏瀑布：secret 字段值拒绝写入日志 detail
SENSITIVE_KEYS = {"password", "password_hash", "token", "secret", "api_key", "apikey", "key", "authorization"}


def scrub(value: Any, depth: int = 0) -> Any:
    """递归脱敏：命中敏感键的值替换为 ***。"""
    if depth > 8:
        return "…"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if str(k).lower() in SENSITIVE_KEYS:
                out[k] = "***"
            else:
                out[k] = scrub(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v, depth + 1) for v in value[:50]]
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "…"
    return value
