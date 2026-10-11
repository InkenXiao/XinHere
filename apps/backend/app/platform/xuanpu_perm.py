# XuanPu 统一权限体系对接（服务间 API）
# ----------------------------------------------------------------------------
# 权限点由 XinHere 代码功能面抽取（与 XuanPu 权限目录 XinHere 域一一对应）：
#   xinhere.use            平台访问（登录门槛）
#   xinhere.chat           AI 对话问答（Xin语：联网搜索/任务派发）
#   xinhere.cockpit.manage 驾驶舱页面维护（卡片增删/拖拽排序）
#   xinhere.data.query     AI 问数（数据集目录/固化取数/只读SQL/经营总览）
#   xinhere.kb.search      知识检索（本地知识库/XuanPu 知识/青山模式）
#   xinhere.skill.run      技能执行（本地+市场技能、驾驶舱技能卡）
#   xinhere.meeting        实时会议（录音/转写/纪要）
#   xinhere.agent.bridge   XuanPu 协作（待办/填报/IM/分身对话/项目看板）
#   xinhere.finance.manage 财务管理（填报审核/指标考核/风险填报/现金担保复核）
#
# 消费方式：
#   - SSO 登录时全量拉取生效权限（permissions 空 → resolve 返回全部 allow 清单）
#   - 前端按权限加载功能（编辑开关/会议卡/技能卡等）
#   - 问答工具执行与财务端点按权限访问控制（require_perm / 工具内 has_perm）
# 缓存：进程内 60s TTL（与 XuanPu 侧判定缓存一致）；本地账号（未绑定统一身份）视为全权限。
from __future__ import annotations

import logging
import time

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

PERM_USE = "xinhere.use"
PERM_CHAT = "xinhere.chat"
PERM_COCKPIT_MANAGE = "xinhere.cockpit.manage"
PERM_DATA_QUERY = "xinhere.data.query"
PERM_KB_SEARCH = "xinhere.kb.search"
PERM_SKILL_RUN = "xinhere.skill.run"
PERM_MEETING = "xinhere.meeting"
PERM_AGENT_BRIDGE = "xinhere.agent.bridge"
PERM_FINANCE_MANAGE = "xinhere.finance.manage"

# 全量权限点（登录响应 / 本地账号放行清单）
ALL_PERMS: list[str] = [
    PERM_USE, PERM_CHAT, PERM_COCKPIT_MANAGE, PERM_DATA_QUERY, PERM_KB_SEARCH,
    PERM_SKILL_RUN, PERM_MEETING, PERM_AGENT_BRIDGE, PERM_FINANCE_MANAGE,
]

_CACHE_TTL = 60.0
# 进程内缓存: xuanpu_user_id -> (拉取时间, 权限清单)
_perm_cache: dict[str, tuple[float, list[str]]] = {}


def _fetch_raw(xuanpu_user_id: str) -> list[str] | None:
    """调 XuanPu 判定端点拉取用户全部生效权限；失败返回 None（调用方降级）。"""
    url = settings.xuanpu_perm_url.strip()
    token = settings.xuanpu_perm_token.strip()
    if not url or not token or not xuanpu_user_id:
        return None
    try:
        resp = httpx.post(
            url,
            headers={"X-Internal-Token": token},
            json={"user_id": int(xuanpu_user_id), "permissions": []},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return list(data.get("permissions") or [])
    except Exception as exc:  # noqa: BLE001 - 权限服务任何异常均降级
        logger.warning("XuanPu 权限拉取失败（降级为全量权限）: %s", exc)
        return None


def user_permissions(xuanpu_user_id: str | None) -> list[str]:
    """用户生效权限清单（xinhere.* 域过滤 + 60s 缓存）。

    - 未绑定统一身份的本地账号：全量权限（开发/管理账号）
    - XuanPu 权限服务不可用：降级全量（不阻断已登录用户）
    - 拉取成功：仅保留 xinhere.* 权限点（XuanPu 自身权限点与 XinHere 无关）
    """
    if not xuanpu_user_id:
        return list(ALL_PERMS)
    now = time.monotonic()
    cached = _perm_cache.get(xuanpu_user_id)
    if cached and now - cached[0] < _CACHE_TTL:
        return list(cached[1])
    raw = _fetch_raw(xuanpu_user_id)
    if raw is None:
        return list(ALL_PERMS)
    perms = sorted({p for p in raw if p.startswith("xinhere.")})
    _perm_cache[xuanpu_user_id] = (now, perms)
    return list(perms)


def has_perm(xuanpu_user_id: str | None, code: str) -> bool:
    """单点权限判定（问答工具执行时访问控制）"""
    return code in user_permissions(xuanpu_user_id)


def resolve_xinhere_role(xuanpu_user_id: str, fallback: str) -> str | None:
    """SSO 登录角色映射：super_admin（全权，含 finance.manage）或
    xinhere.finance.manage → 本部财务；xinhere.use → 被投财务；均无 → 拒绝进入。
    服务不可用时返回 fallback（降级，不拒绝登录）。"""
    raw = _fetch_raw(xuanpu_user_id)
    if raw is None:
        return fallback if fallback == "hq_finance" else "investee_finance"
    perms = {p for p in raw if p.startswith("xinhere.")}
    if PERM_FINANCE_MANAGE in perms:
        return "hq_finance"
    if PERM_USE in perms:
        return "investee_finance"
    return None
