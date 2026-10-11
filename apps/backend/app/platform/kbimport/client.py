"""信投知识库数据接入网关客户端（CoWork 通道，前缀 /api/v1/knowledge-import）。

链路（接口文档 v1.5.29）：POST /cowork/login（X-Service-Id + X-Import-Key + 业务库
用户名）→ cwk_ Bearer（默认 21600 秒）；业务请求带 X-Service-Id + Authorization、
不再带密钥。GET /cowork/tree-children 懒加载：无参=全部可见库（spaces + 各库首层），
folderId=该文件夹单层内容。统一响应 HTTP 200 + {code, data, error}，成功 code=200。

- cwk_ 票按用户名进程级缓存（提前 5 分钟视为过期防临界 401），401 强制重换重试一次；
- 用户名映射：当前平台用户 → 业务库 system_users.username（BIZ_PG_URI 唯一数据源，不缓存）；
- KB_IMPORT_* 未配置整体降级（kb_import_enabled 判空，调用方据此 503）；密钥不落日志。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request

import psycopg

from ...core import errors
from ...core.config import settings

logger = logging.getLogger(__name__)

_PREFIX = "/api/v1/knowledge-import"
_EXP_MARGIN = 300  # 换票提前量（秒）

# biz_username -> (cwk_ token, exp_unix)
_tokens: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


def kb_import_enabled() -> bool:
    return bool(settings.kb_import_base and settings.kb_import_service_id and settings.kb_import_key)


def biz_username(user_id: str, username: str) -> str:
    """当前平台用户 → 业务库 SCMS 用户名（system_users）。
    优先按 username 匹配，回退按数字 id（与 cowork 快捷登录同一映射口径）。"""
    with psycopg.connect(settings.biz_pg_uri, connect_timeout=5) as conn, conn.cursor() as cur:
        if username and username != user_id and not username.isdigit():
            cur.execute(
                "SELECT username FROM system_users WHERE username = %s AND deleted = 0 LIMIT 1",
                (username,),
            )
            row = cur.fetchone()
            if row:
                return row[0]
        cur.execute(
            "SELECT username FROM system_users WHERE id = %s AND deleted = 0 LIMIT 1",
            (int(user_id) if user_id.isdigit() else -1,),
        )
        row = cur.fetchone()
    if not row:
        raise errors.upstream(f"业务库未找到用户 id={user_id} username={username}")
    return row[0]


def _login(biz_user: str) -> tuple[str, float]:
    """换票：返回 (cwk_ token, exp_unix)。密钥仅进请求头，不落日志。"""
    url = f"{settings.kb_import_base.rstrip('/')}{_PREFIX}/cowork/login"
    req = urllib.request.Request(
        url,
        data=json.dumps({"username": biz_user}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Service-Id": settings.kb_import_service_id,
            "X-Import-Key": settings.kb_import_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("知识库网关换票失败 user=%s err=%r", biz_user, exc)
        raise errors.upstream("知识库网关登录失败") from exc
    if body.get("code") != 200:
        logger.warning("知识库网关换票被拒 user=%s code=%s error=%s",
                       biz_user, body.get("code"), body.get("error"))
        raise errors.upstream("知识库网关登录被拒（用户名或通道密钥无效）")
    data = body.get("data") or {}
    token = str(data.get("accessToken") or "")
    try:
        ttl = float(data.get("expiresIn") or 0)
    except (TypeError, ValueError):
        ttl = 0.0
    if not token or ttl <= 0:
        raise errors.upstream("知识库网关登录响应缺少 accessToken/expiresIn")
    return token, time.time() + ttl


def _token_for(biz_user: str) -> str:
    now = time.time()
    with _lock:
        hit = _tokens.get(biz_user)
        if hit and hit[1] - _EXP_MARGIN > now:
            return hit[0]
    token, exp = _login(biz_user)
    with _lock:
        _tokens[biz_user] = (token, exp)
    return token


def token_for(biz_user: str) -> str:
    """公开取票：命中缓存或换票（供 kb_direct_qa 等复用）。"""
    return _token_for(biz_user)


def invalidate_token(biz_user: str) -> None:
    """清掉某用户的缓存票据（401 失效后强制重登用）。"""
    with _lock:
        _tokens.pop(biz_user, None)


def tree_children(biz_user: str, folder_id: int | None = None) -> dict:
    """懒加载树：无参=全部可见库（spaces）；folder_id=该文件夹单层内容。
    401（票据被吊销/服务端重启等）清缓存强制换票重试一次。"""
    if not kb_import_enabled():
        raise errors.upstream("KB_IMPORT_* 未配置（知识库范围功能已关闭）")
    base = f"{settings.kb_import_base.rstrip('/')}{_PREFIX}/cowork/tree-children"
    for attempt in (0, 1):
        token = _token_for(biz_user)
        url = f"{base}?folderId={folder_id}" if folder_id is not None else base
        req = urllib.request.Request(
            url,
            headers={
                "X-Service-Id": settings.kb_import_service_id,
                "Authorization": f"Bearer {token}",
            },
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            logger.info("知识库网关 ← GET tree-children folder=%s 耗时=%.1fs code=%s",
                        folder_id, time.time() - t0, body.get("code"))
            if body.get("code") != 200:
                raise errors.upstream(
                    f"知识库网关返回错误：{body.get('error') or body.get('code')}")
            return body.get("data") or {}
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                with _lock:
                    _tokens.pop(biz_user, None)
                logger.info("知识库网关 401（票据失效），重换重试 user=%s", biz_user)
                continue
            logger.warning("知识库网关 tree-children 失败 http=%s folder=%s", exc.code, folder_id)
            raise errors.upstream("知识库网关请求失败") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("知识库网关不可达 folder=%s err=%r", folder_id, exc)
            raise errors.upstream("知识库网关不可达") from exc
    raise RuntimeError("unreachable")
