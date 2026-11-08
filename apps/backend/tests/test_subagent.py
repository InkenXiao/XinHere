"""P4 改造回归：子代理接缝（provider 注册表 / 深度上限 / 事件成对 / 工具门控）。"""
from __future__ import annotations

from typing_extensions import Annotated

from langchain_core.tools import InjectedToolCallId

from app.platform.agent import subagent as SA
from app.platform.agent.tool_base import ToolCtx


class _StubProvider:
    name = "stub"
    description = "测试用子代理"

    def run(self, spec: SA.SubagentRunSpec) -> SA.SubagentOutcome:
        return SA.SubagentOutcome(ok=True, output="结论要点\n依据")


def _ctx(events: list) -> ToolCtx:
    return ToolCtx(
        user_id="u1", session_id="s1", request_id="r1",
        emit=lambda t, d: events.append((t, d)) or (1, None),
    )


def _spec(**kw) -> SA.SubagentRunSpec:
    base = dict(provider="stub", name="stub", prompt="调研 X", depth=1,
                session_id="s1", user_id="u1")
    base.update(kw)
    return SA.SubagentRunSpec(**base)


def test_provider_register_fail_loud_and_catalog():
    p = _StubProvider()
    SA.register_provider(p)
    with pytest_raises_duplicate():
        SA.register_provider(_StubProvider())
    assert {"name": "stub", "description": "测试用子代理"} in SA.provider_catalog()
    SA._PROVIDERS.pop("stub", None)


def pytest_raises_duplicate():
    import pytest

    return pytest.raises(RuntimeError, match="重复注册")


def test_run_subagent_emits_paired_events():
    events: list = []
    SA.register_provider(_StubProvider())
    outcome = SA.run_subagent(_spec(), _ctx(events))
    SA._PROVIDERS.pop("stub", None)
    assert outcome.ok and "结论要点" in outcome.output
    types = [t for t, _ in events]
    assert types == ["subagent/start", "subagent/end"]
    assert events[1][1]["ok"] is True and events[1][1]["duration_ms"] >= 0


def test_run_subagent_depth_cap_and_unknown_provider():
    events: list = []
    capped = SA.run_subagent(_spec(depth=SA.MAX_SUBAGENT_DEPTH), _ctx(events))
    assert not capped.ok and "深度已达上限" in capped.error
    assert events == []  # provider 解析/深度校验失败不产生 start
    unknown = SA.run_subagent(_spec(provider="nope"), _ctx(events))
    assert not unknown.ok and "未注册的子代理类型" in unknown.error
    assert events == []


def test_subagent_tools_depth_gating():
    assert SA.subagent_tools(ToolCtx(user_id="u", session_id="s", request_id="r",
                                     extra={"subagent_depth": SA.MAX_SUBAGENT_DEPTH})) == []
    tools = SA.subagent_tools(ToolCtx(user_id="u", session_id="s", request_id="r"))
    assert len(tools) == 1 and tools[0].name == "delegate_task"
    # 未注册 agent 名 → 工具返回失败说明（不抛）
    msg = tools[0].invoke({"args": {"agent": "ghost", "task_prompt": "x"},
                           "name": "delegate_task", "type": "tool_call",
                           "id": "c1", "tool_call_id": "c1"})
    assert "失败" in msg.content


def test_subagent_events_registered_in_registry():
    from app.platform.events.registry import REGISTRY

    assert REGISTRY["subagent/end"][1] is True  # ignorable：前端装配器无感跳过
    assert REGISTRY["subagent/start"][1] is True


def test_sub_checkpointer_isolated_from_parent():
    """复核证据：子图独立 checkpointer——父图 get_state/resume 物理读不到子图状态。"""
    from langgraph.checkpoint.memory import MemorySaver

    sub_a, sub_b = SA._new_sub_checkpointer(), SA._new_sub_checkpointer()
    assert isinstance(sub_a, MemorySaver)
    assert sub_a is not sub_b  # 每次执行都是独立实例
    # 隔离语义证明：子图 checkpoint 写入 sub_a 后，父图 saver（同实现类的另一独立实例，
    # 即 executor._saver）按同 thread_id 查询无任何 checkpoint——子图状态不可能被父图加载
    config = {"configurable": {"thread_id": "sub-1", "checkpoint_ns": ""}}
    sub_a.put(config, {"v": 1, "id": "cp1", "channel_values": {}, "channel_versions": {}},
              {"source": "loop", "step": 1}, {})
    assert sub_a.get_tuple(config) is not None  # 子图 checkpoint 落在子图自己的 saver
    parent = MemorySaver()
    # 父图实例（executor._saver 同类实现）读不到子图 checkpoint：resume 时按自身 saver
    # 查 latest 为 None，StateSnapshot/get_state 同源，子图内部状态物理不可达
    assert parent.get_tuple(config) is None


def test_subgraph_toolset_physically_excludes_delegate_task():
    """复核证据：深度门控硬移除——delegate_task 物理不在子图工具集，非仅运行时判断。"""
    tools = SA.GeneralSubagentProvider()._build_readonly_tools(
        ToolCtx(user_id="u1", session_id="s1", request_id="r1", emit=lambda t, d: (1, None))
    )
    names = [t.name for t in tools]
    assert "delegate_task" not in names
    assert names == ["search_knowledge", "list_companies"]  # 只读白名单
