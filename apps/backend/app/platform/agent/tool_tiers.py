"""P8a 工具 tier 注册表与分级 guard 链。

三段语义：
- ``read``：只读工具——权限校验 + 审计（现状 guard，无额外闸门）；
- ``write``：写操作工具——read 之上强制走 tool_scope 的审计/回滚链（现状即如此，
  tier 只是把语义显式化，便于统计与子代理收窄判断）；
- ``high_impact``：高影响外溢工具（对外发消息、对外派发等）——执行前必须存在
  本会话本轮的 ``approval/granted`` 事件（审批结果显式落日志，可回放），否则拒绝。

注册规则：
- 未显式注册的工具一律 ``read``（fail-safe：不误伤）；
- ``register_tool_tier`` 同名同值幂等；同名改值 fail-loud（装配期冲突早暴露）；
- 插件可在 make_tools 阶段声明 tier；动态名（mcp_* 等）按实际最终名注册。

子代理权限规则（只收窄不扩张）见 subagent.py：子代理工具集必须是父级工具名的
真子集，且 GeneralSubagentProvider 只装配只读白名单。
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from ...persistence.models import PlatformSessionEvent

logger = logging.getLogger(__name__)

TIERS = ("read", "write", "high_impact")

# 显式 tier 声明（工具最终名 → tier）。read 默认，无需登记；这里只登记 write 与 high_impact。
_TOOL_TIERS: dict[str, str] = {
    # ---- 写操作（tools_scms）：业务状态变更 ----
    "arrange_task": "write",
    "request_todo_done": "write",
    "request_todo_na": "write",
    "review_submissions": "write",
    "confirm_na_requests": "write",
    "remind_pending": "write",
    "append_task_assignees": "write",
    "end_task_flow": "write",
    "dispatch_task": "write",
    "dispatch_generic_task": "write",
    "fill_todo_payload": "write",
    "pre_review_todos": "write",
    "open_post_report": "write",
    "open_fin_risk_report": "write",
    "open_milestone_audit": "write",
    "generate_html_report": "write",
    "generate_fin_risk_report": "write",
    # ---- 写操作（xuanpu 外部平台） ----
    "xuanpu_create_todo": "write",
    "xuanpu_fill_create": "write",
    "xuanpu_fill_submit": "write",
    "xuanpu_skill_run": "write",
    "xuanpu_skill_script": "write",
    "xuanpu_tool_run": "write",
    "xuanpu_mcp_call": "write",
    # ---- 写操作（技能层） ----
    "run_skill_script": "write",
    "write_skill_file": "write",
}
# high_impact 初始为空：机制先落地（guard + 审批事件 + 测试证据），
# 具体工具的分级随审批卡接入逐步登记（register_tool_tier(name, "high_impact")），
# 避免一次性把存量外发工具全部锁死、破坏现有流程。


def register_tool_tier(name: str, tier: str) -> None:
    """注册/声明工具 tier。同名同值幂等；同名改值 fail-loud；未知 tier fail-loud。"""
    if tier not in TIERS:
        raise ValueError(f"未知工具 tier: {tier}（允许：{'/'.join(TIERS)}）")
    existing = _TOOL_TIERS.get(name)
    if existing is not None and existing != tier:
        raise RuntimeError(f"工具 tier 声明冲突: {name} 已注册为 {existing}，拒绝改为 {tier}")
    _TOOL_TIERS[name] = tier


def tool_tier(name: str) -> str:
    """查询工具 tier；未登记一律 read。"""
    return _TOOL_TIERS.get(name, "read")


def has_approval_event(session_id: str, tool_or_kind: str, turn: int | None = None) -> bool:
    """本会话是否存在匹配的 approval/granted 事件（tool 字段或 scope 字段命中）。
    turn > 0 时限定本轮；审批结果经 sessions 落库（approval/* 显式事件），可回放。"""
    if not session_id or not tool_or_kind:
        return False
    from ...persistence.session import SessionLocal

    try:
        with SessionLocal() as db:
            q = select(PlatformSessionEvent).where(
                PlatformSessionEvent.session_id == session_id,
                PlatformSessionEvent.type == "approval/granted",
            )
            if turn:
                q = q.where(PlatformSessionEvent.turn == int(turn))
            rows = db.scalars(q.order_by(PlatformSessionEvent.seq)).all()
            for row in rows:
                data = row.data or {}
                if tool_or_kind in {data.get("tool"), data.get("scope")}:
                    return True
            return False
    except Exception:
        logger.warning("审批事件查询失败（按未审批处理）sid=%s tool=%s", session_id, tool_or_kind,
                       exc_info=True)
        return False


def guard_high_impact(session_id: str, tool_name: str, turn: int | None = None) -> None:
    """high_impact 工具 pre-execute 闸门：无本会话审批事件 → 拒绝执行。
    抛 ToolDeniedError；模型侧收到明确的拒绝信息（可在审批后重试）。"""
    if not has_approval_event(session_id, tool_name, turn):
        raise ToolDeniedError(
            f"工具 {tool_name} 属于高影响操作，需用户审批后才能执行；"
            "请改用审批确认卡（先向用户说明操作内容并请求确认）。"
        )


class ToolDeniedError(PermissionError):
    """高影响工具未获审批被拒（工具执行中止，业务零副作用）。"""
