"""P8a 验收：tier 注册 fail-loud / high_impact 审批闸门（无审批拒绝、有审批放行）/
审批事件可从会话日志回放 / 子代理工具权限是父级真子集。"""
from __future__ import annotations

import pytest

from app.platform.agent import otel, subagent as SA
from app.platform.agent import tool_tiers as TT
from app.platform.agent.tool_base import ToolCtx, tool_scope
from app.platform.events.registry import REGISTRY
from app.platform.events.store import store


@pytest.fixture(autouse=True)
def _otel_inmemory():
    """tool_scope 会产生 OTel span：注入 InMemory exporter，避免 tracer() 兜底 console。"""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    otel.reset_for_tests()
    otel.setup_otel(exporter=InMemorySpanExporter())
    yield
    otel.shutdown_otel()
    otel.reset_for_tests()


@pytest.fixture
def hi_tool():
    """合成 high_impact 工具名（不碰存量登记）；测完撤销，避免污染进程级注册表。"""
    TT.register_tool_tier("tt_hi_probe", "high_impact")
    yield "tt_hi_probe"
    TT._TOOL_TIERS.pop("tt_hi_probe", None)


def _ctx(session_id: str) -> ToolCtx:
    return ToolCtx(user_id="pytest", session_id=session_id, request_id="r1")


def test_default_tier_and_registry_shape():
    assert TT.TIERS == ("read", "write", "high_impact")
    assert TT.tool_tier("ghost_tool") == "read"      # 未登记一律 read（fail-safe）
    assert TT.tool_tier("arrange_task") == "write"
    assert REGISTRY["approval/granted"][1] is True   # ignorable：落库可回放，前端无渲染器时跳过
    assert REGISTRY["approval/denied"][1] is True


def test_register_fail_loud():
    with pytest.raises(ValueError, match="未知工具 tier"):
        TT.register_tool_tier("tt_x", "critical")
    TT.register_tool_tier("tt_tier_probe", "write")
    TT.register_tool_tier("tt_tier_probe", "write")  # 同名同值幂等
    with pytest.raises(RuntimeError, match="tier 声明冲突"):
        TT.register_tool_tier("tt_tier_probe", "read")
    TT._TOOL_TIERS.pop("tt_tier_probe", None)
    with pytest.raises(RuntimeError, match="tier 声明冲突"):
        TT.register_tool_tier("arrange_task", "read")  # 存量登记改值同样 fail-loud


def test_high_impact_denied_without_approval(mk_session, hi_tool):
    sid = mk_session()
    executed: list = []
    with pytest.raises(TT.ToolDeniedError, match="高影响"):
        with tool_scope(_ctx(sid), "tt_hi_probe", "c1", {}) as _db:
            executed.append(True)
    assert executed == []  # 拒绝发生在任何业务执行之前（业务零副作用）


def test_high_impact_allowed_after_granted_event(mk_session, hi_tool):
    sid = mk_session()
    store.append(sid, "approval/granted", {
        "scope": "tt_hi_probe", "tool": "tt_hi_probe", "turn": 0, "summary": "允许执行",
    })
    executed: list = []
    with tool_scope(_ctx(sid), "tt_hi_probe", "c1", {}) as _db:
        executed.append(True)
    assert executed == [True]
    assert TT.has_approval_event(sid, "tt_hi_probe") is True
    assert TT.has_approval_event(sid, "tt_other_tool") is False


def test_approval_events_replayable_from_log(mk_session):
    """验收：审批事件作为显式会话事件落日志，可从 list_events 回放；turn 限定匹配。"""
    sid = mk_session()
    store.append(sid, "approval/granted", {"scope": "card_a", "summary": "确认"}, turn=1)
    store.append(sid, "approval/denied", {"scope": "card_b", "reason": "取消"}, turn=2)
    rows, _more = store.list_events(sid)
    approvals = [r for r in rows if r.type in ("approval/granted", "approval/denied")]
    assert [r.type for r in approvals] == ["approval/granted", "approval/denied"]
    assert approvals[0].data["scope"] == "card_a" and approvals[0].data["summary"] == "确认"
    assert approvals[0].turn == 1
    assert approvals[1].data["reason"] == "取消" and approvals[1].turn == 2
    # guard 的本轮语义：turn 限定时只认本轮的审批
    assert TT.has_approval_event(sid, "card_a", turn=1) is True
    assert TT.has_approval_event(sid, "card_a", turn=2) is False


def test_child_tools_subset_fail_loud():
    # 合法子集：只读白名单 ⊆ 父级全集
    SA.assert_child_tools_subset(
        ["search_knowledge", "list_companies"],
        ["search_knowledge", "list_companies", "delegate_task", "arrange_task"],
    )
    # 越权：子代理持有父级没有的写工具 → fail-loud
    with pytest.raises(RuntimeError, match="只收窄不扩张"):
        SA.assert_child_tools_subset(["arrange_task"], ["search_knowledge"])
    # 父级未携带工具全集（外部一次性执行方）：无法校验时放行
    SA.assert_child_tools_subset(["anything"], [])


def test_subagent_readonly_tools_are_true_subset():
    """验收：子代理工具权限是父级真子集——readonly 白名单对含写工具的父集均为子集，
    且 delegate_task 物理不在子图工具集（P4 硬移除在 P8a 语义下依旧成立）。"""
    tools = SA.GeneralSubagentProvider()._build_readonly_tools(
        ToolCtx(user_id="pytest", session_id="s-pp", request_id="r1")
    )
    names = [t.name for t in tools]
    assert "delegate_task" not in names
    SA.assert_child_tools_subset(
        names, [*names, "delegate_task", "arrange_task", "dispatch_task"])
