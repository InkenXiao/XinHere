"""用户 accessToken 校验与透传（SCMS OAuth2 check-token，透传模式核心）。

- 登录代理成功即 seed（token → 身份），此后 TTL 窗口内免验真；窗口过后逐 token 实调
  check-token 验真，结果缓存 TTL 同时受 token exp 与固定上限约束，避免每请求打 SCMS。
- 有效判定遵循对接文档：code==0 且 user_id 非空 且 exp>now；网络异常 fail-closed 拒绝。
- 同一 token 经 bind_user_token 绑定请求上下文，作为业务 SCMS 调用（指标/问数）首选凭证；
  agent 后台线程不传播 contextvars，由 RunHandle 显式携带后在线程内重绑。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextvars import ContextVar
from dataclasses import dataclass

from ...core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class TokenIdentity:
    user_id: str
    username: str = ""  # check-token 不回传，仅登录 seed 时已知；重启后为空属正常
    tenant_id: str = ""
    exp: float = 0.0
    cached_at: float = 0.0


_cache: dict[str, TokenIdentity] = {}
_lock = threading.Lock()

_user_token: ContextVar[str] = ContextVar("scms_user_token", default="")


def current_user_token() -> str:
    return _user_token.get()


def bind_user_token(token: str):
    return _user_token.set(token)


def reset_user_token(tk) -> None:
    _user_token.reset(tk)


def _alive(ident: TokenIdentity, now: float) -> bool:
    if settings.app_env == "dev" and ident.exp > now:
        return True
    return ident.exp > now and now < ident.cached_at + settings.scms_token_cache_ttl


def seed_login(token: str, *, user_id, username: str = "", tenant_id: str = "",
               expires_ms: float = 0.0) -> None:
    """登录代理成功后预填缓存（SCMS 自己签发的响应，可信源）；exp 未知按一个 TTL 窗口估。"""
    if not token or not user_id:
        return
    now = time.time()
    exp = expires_ms / 1000.0 if expires_ms else now + settings.scms_token_cache_ttl
    with _lock:
        _cache[token] = TokenIdentity(
            user_id=str(user_id), username=username, tenant_id=str(tenant_id or ""),
            exp=exp, cached_at=now,
        )


def identity_of(token: str) -> TokenIdentity | None:
    """查缓存身份（不触发远程验真）：报告嵌入卡取 tenant_id 等用。"""
    if not token:
        return None
    with _lock:
        return _cache.get(token)


def check_token(token: str) -> TokenIdentity | None:
    """验真并缓存；无效/过期/SCMS 不可达一律返回 None（fail-closed）。"""
    if not token:
        return None
    now = time.time()
    with _lock:
        hit = _cache.get(token)
    if hit and _alive(hit, now):
        return hit
    data = _check_remote(token)
    if data is None:
        return None
    user_id = str(data.get("user_id") or "")
    try:
        exp = float(data.get("exp") or 0)
    except (TypeError, ValueError):
        exp = 0.0
    if not user_id or exp <= now:
        return None
    with _lock:
        prev = _cache.get(token)
        _cache[token] = TokenIdentity(
            user_id=user_id,
            # check-token 响应自带 username（芋道标准字段）；缓存里已有同用户的更可信
            username=(prev.username if prev and prev.user_id == user_id else "")
                     or str(data.get("username") or ""),
            tenant_id=str(data.get("tenant_id") or ""),
            exp=exp, cached_at=now,
        )
        if len(_cache) > 1024:  # 顺手清扫过期项，防无界增长
            for k in [k for k, v in _cache.items() if v.exp <= now]:
                _cache.pop(k, None)
    return _cache[token]


def _check_remote(token: str) -> dict | None:
    """POST /admin-api/system/oauth2/check-token；Basic 凭据（agent-system）仅服务端持有。"""
    req = urllib.request.Request(
        f"{settings.scms_auth_base.rstrip('/')}/admin-api/system/oauth2/check-token",
        data=urllib.parse.urlencode({"token": token}).encode("utf-8"),
        headers={
            "Authorization": settings.scms_check_basic,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.info("check-token 拒绝 http=%s", exc.code)
        return None
    except Exception as exc:
        logger.warning("check-token 不可达（fail-closed 拒绝） err=%r", exc)
        return None
    return body.get("data") if body.get("code") == 0 else None
