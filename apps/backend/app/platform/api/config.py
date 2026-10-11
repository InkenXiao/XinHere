"""导航入口配置（免鉴权：登录页 cowork 按钮也要用）。当前阶段全部纯 URL 直跳。"""
from __future__ import annotations

import base64
import logging
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends


from ...core import errors
from ...core.config import settings
from ...persistence.models import SysUser
from .deps import current_user

router = APIRouter(tags=["config"])
logger = logging.getLogger(__name__)


@router.get("/config/navs")
def navs():
    items = []
    if settings.nav_biz_url:
        items.append({"key": "biz", "name": "业务系统", "kind": f"url:{settings.nav_biz_url}"})
    # 业务系统（SCMS 财务后台）快捷登录：配了scms_sso_base 才出现，点击由前端调 /sso/scms-launch 出票
    if settings.scms_sso_base:
        items.append({"key": "scms", "name": "业务系统", "kind": "sso:scms"})
    if settings.nav_kb_url:
        items.append({"key": "kb", "name": "青山知识库", "kind": "sso:kb"})
    # cowork：配置了 NAV_COWORK_URL 则直跳该地址（纯 URL 不拼参）；未配置跳站内敬请期待页
    if settings.nav_cowork_url:
        items.append({"key": "cowork", "name": "TIDE CoWork", "kind": f"url:{settings.nav_cowork_url}"})
    else:
        items.append({"key": "cowork", "name": "TIDE CoWork", "kind": "sso:cowork"})
    # 未登录兜底：cowork 直达入口（登录页底部按钮用）
    if settings.nav_cowork_login_url:
        items.append({"key": "cowork-login", "name": "cowork",
                      "kind": f"url:{settings.nav_cowork_login_url}"})
    return {"items": items}


# 首页驾驶舱「业务系统」卡片已迁移至 cockpit 配置表（cockpit_groups/cockpit_cards，
# surface=dashboard，见 platform/api/cockpit.py；前端改调 GET /cockpit/cards?surface=dashboard）。


@router.get("/config/report-center")
def report_center():
    """驾驶舱 AI工具「报告中心」卡入口：{report_embed_base}/report-center（老系统报告中心，
    自带 redirect → /report-center/ppt）。免登走老系统 token 交接协议，由前端拼
    ?token=…#refreshToken=…&tenantId=…（与顶栏 url: 导航同构）后新标签打开；
    refresh/tenant 仅存前端，后端只下发基址。未配置返回 url=None，前端隐藏入口。"""
    if not settings.report_embed_base:
        return {"url": None}
    return {"url": f"{settings.report_embed_base.rstrip('/')}/workbench"}


def _simple_launch_cipher(key: str, user_id: str, exp: int) -> str:
    """AES-128-ECB(PKCS7) 加密 "{user_id}|{exp}"，Base64URL 无 padding（简易票据通用格式）。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    kb = key.encode("utf-8")
    if len(kb) != 16:
        raise errors.upstream("SSO AES key 必须为 16 字节（AES-128）")
    plain = f"{user_id}|{exp}".encode("utf-8")
    pad = 16 - len(plain) % 16  # PKCS7（块长 16）
    padded = plain + bytes([pad]) * pad
    enc = Cipher(algorithms.AES(kb), modes.ECB()).encryptor()
    ct = enc.update(padded) + enc.finalize()
    return base64.urlsafe_b64encode(ct).rstrip(b"=").decode("ascii")


@router.get("/sso/kb-launch")
def kb_launch(user: SysUser = Depends(current_user)):
    """青山知识库快捷登录链接 → {kb_sso_base}/#/sso/launch?s=…（hash 路由）。
    完整 URL 返回给前端直接跳转。
    （门户按一期 system_users 的用户标识校验，用 user_id 而非登录名。）"""
    exp = int(time.time()) + settings.kb_sso_ttl_hours * 3600
    s = _simple_launch_cipher(settings.kb_sso_aes_key, user.user_id, exp)
    url = f"{settings.kb_sso_base.rstrip('/')}/#/sso/launch?s={quote(s, safe='')}"
    logger.info("知识库快捷登录出票 user_id=%s exp=%d", user.user_id, exp)
    return {"url": url}


@router.get("/sso/scms-launch")
def scms_launch(redirect: str | None = None, user: SysUser = Depends(current_user)):
    """SCMS 财务后台快捷登录链接 → {scms_sso_base}/sso/launch?s=…（history 路由，不带 #）。
    同一套简易票据；SCMS 按 system_users 主键验票（透传登录下 user_id 即 SCMS 数字主键），
    有效期不得超过 SCMS 侧 simple-launch-max-ttl-seconds（默认 24h），否则判"有效期异常"拒绝。
    redirect：SCMS 站内落地路径（如 /finance-warning/finance-statistics-sample），透传拼接在票据后。"""
    if not settings.scms_sso_base:
        raise errors.upstream("scms_sso_base 未配置")
    exp = int(time.time()) + settings.scms_sso_ttl_hours * 3600
    s = _simple_launch_cipher(settings.scms_sso_aes_key, user.user_id, exp)
    url = f"{settings.scms_sso_base.rstrip('/')}/sso/launch?s={quote(s, safe='')}"
    if redirect:
        url = f"{url}&redirect={quote(redirect, safe='')}"
    logger.info("SCMS 快捷登录出票 user_id=%s exp=%d redirect=%s", user.user_id, exp, redirect or "-")
    return {"url": url}
