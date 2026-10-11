"""cowork 快捷登录：出票（登录态）+ code 兑换（对外免鉴权）。

code = AES-128-ECB(PKCS7) 加密 "{timestamp}|{username}|{password}"，Base64URL 无 padding，
算法与 kb-launch 一致、密钥独立（cowork_code_aes_key）。兑换端不校验过期（2B 内部约定）。
"""
from __future__ import annotations

import base64
import binascii
import logging
import time

import psycopg
from fastapi import APIRouter, Depends, Query, Request


from ...core import errors
from ...core.config import settings
from ...core.errors import AppError
from ...persistence.models import SysUser
from .deps import current_user

router = APIRouter(tags=["cowork-sso"])
logger = logging.getLogger(__name__)


def _aes_ecb(data: bytes, *, decrypt: bool) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = settings.cowork_code_aes_key.encode("utf-8")
    if len(key) != 16:
        raise errors.upstream("cowork_code_aes_key 必须为 16 字节（AES-128）")
    op = Cipher(algorithms.AES(key), modes.ECB()).decryptor() if decrypt \
        else Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return op.update(data) + op.finalize()


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise errors.validation("code 格式不合法")
    pad = data[-1]
    if not 1 <= pad <= 16 or data[-pad:] != bytes([pad]) * pad:
        raise errors.validation("code 格式不合法")
    return data[:-pad]


def _encrypt_code(username: str, password: str) -> str:
    plain = f"{int(time.time())}|{username}|{password}".encode("utf-8")
    pad = 16 - len(plain) % 16  # PKCS7（块长 16）
    ct = _aes_ecb(plain + bytes([pad]) * pad, decrypt=False)
    return base64.urlsafe_b64encode(ct).rstrip(b"=").decode("ascii")


def _decrypt_code(code: str) -> tuple[int, str, str]:
    try:
        ct = base64.urlsafe_b64decode(code + "=" * (-len(code) % 4))
        plain = _pkcs7_unpad(_aes_ecb(ct, decrypt=True)).decode("utf-8")
        ts_s, username, password = plain.split("|", 2)
        return int(ts_s), username, password
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise errors.validation("code 解密失败")


def _query_biz_user(user_id: str, username: str) -> tuple[str, str]:
    """业务库直连查 system_users 的 (username, password)（BCrypt 哈希）。
    check-token 可能不回传 username（回退成数字 user_id），故优先按 id 匹配，
    username 与 user_id 不同才回退按 username 查。"""
    with psycopg.connect(settings.biz_pg_uri, connect_timeout=5) as conn, conn.cursor() as cur:
        if username and username != user_id and not username.isdigit():
            cur.execute(
                "SELECT username, password FROM system_users WHERE username = %s AND deleted = 0 LIMIT 1",
                (username,),
            )
            row = cur.fetchone()
            if row:
                return row
        cur.execute(
            "SELECT username, password FROM system_users WHERE id = %s AND deleted = 0 LIMIT 1",
            (int(user_id) if user_id.isdigit() else -1,),
        )
        row = cur.fetchone()
    if not row:
        raise errors.upstream(f"业务库未找到用户 id={user_id} username={username}")
    return row


@router.get("/sso/cowork-launch")
def cowork_launch(request: Request, user: SysUser = Depends(current_user)):
    """cowork 快捷登录链接：查业务库密码 → 出 code → 拼完整跳转 URL。
    code（ticket）以 ?conv= 参数拼接，对端按 conv 取票兑换。
    若依规范返回 {code, data, msg}（成功 code=200）；错误统一 HTTP 200 + 业务码。"""
    if not settings.nav_cowork_url:
        return {"code": 500, "data": None, "msg": "NAV_COWORK_URL 未配置（cowork 快捷登录已关闭）"}
    try:
        biz_username, password = _query_biz_user(user.user_id, user.username)
        code = _encrypt_code(biz_username, password)
    except AppError as e:
        logger.warning("cowork 出票失败 username=%s err=%s", user.username, e.message)
        return {"code": 500, "data": None, "msg": e.message}
    url = f"{settings.nav_cowork_url.rstrip('/')}?conv={code}"
    logger.info("cowork 快捷登录出票 username=%s", user.username)
    return {"code": 200, "data": {"url": url}, "msg": "操作成功"}


@router.get("/external/code-exchange")
def code_exchange(code: str = Query(min_length=8)):
    """对外兑换：解密 code，若依规范返回 {code, data, msg}（成功 code=200）。
    免鉴权（2B 内部约定，不校验过期）；错误统一 HTTP 200 + 业务码。"""
    try:
        ts, username, password = _decrypt_code(code)
    except AppError:
        return {"code": 400, "data": None, "msg": "code 解密失败"}
    logger.info("cowork code 兑换 username=%s", username)
    return {"code": 200, "data": {"timestamp": ts, "username": username, "password": password},
            "msg": "操作成功"}
