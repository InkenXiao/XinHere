"""P7a 验收：请求侧 tool-result 裁剪——配对不破坏 / 保留最近 N 轮与首条任务约束 /
裁剪标记 / checkpoint 原对象只读 / 预算内不动作 / 窗口内二次收缩 / compaction 观测。"""
from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")

from langchain_core.messages import (  # noqa: E402
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from app.core.config import settings  # noqa: E402
from app.platform.agent import otel, trimming  # noqa: E402


def _big(tag: str, n: int = 2000) -> str:
    return f"{tag}：" + "长" * n


def _ai_call(call_id: str, name: str = "probe") -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": {}, "id": call_id, "type": "tool_call"}])


def _result(call_id: str, body: str) -> ToolMessage:
    return ToolMessage(content=body, tool_call_id=call_id)


def _convo(big: str, small: str = "窗口内结果。") -> list:
    """13 条会话：System / 首条任务约束 / 陈旧两轮（大正文）/ 最近两轮（小正文）。

    recent_turns=2 → 窗口起点 = 倒数第 2 个 HumanMessage（idx6），
    陈旧 tool result = idx3、idx5；窗口内 = idx8、idx12（最后一条）。
    """
    return [
        SystemMessage(content="你是项目助手。"),                # 0
        HumanMessage(content="任务约束：只输出要点，不改数据。"),  # 1 首条 human
        _ai_call("c1"),                                        # 2
        _result("c1", big),                                    # 3 陈旧 → 裁
        _ai_call("c2"),                                        # 4
        _result("c2", big),                                    # 5 陈旧 → 裁
        HumanMessage(content="第二轮：补充依据"),                # 6 窗口起点
        _ai_call("c3"),                                        # 7
        _result("c3", small),                                  # 8 窗口内
        HumanMessage(content="最后一轮：给出结论"),              # 9 最近轮
        AIMessage(content="好的。"),                            # 10
        _ai_call("c4"),                                        # 11
        _result("c4", small),                                  # 12 最后一条 tool result
    ]


@pytest.fixture
def trim_settings(monkeypatch):
    monkeypatch.setattr(settings, "agent_trim_enabled", True)
    monkeypatch.setattr(settings, "agent_trim_budget_tokens", 300)
    monkeypatch.setattr(settings, "agent_trim_recent_turns", 2)
    monkeypatch.setattr(settings, "agent_trim_tool_head_chars", 20)
    monkeypatch.setattr(settings, "agent_trim_tool_tail_chars", 20)


@pytest.fixture(autouse=True)
def otel_mem():
    """全模块注入 InMemory exporter：裁剪会发 compaction span，避免 tracer() 兜底 console。"""
    otel.reset_for_tests()
    exporter = InMemorySpanExporter()
    otel.setup_otel(exporter=exporter)
    yield exporter
    otel.shutdown_otel()
    otel.reset_for_tests()


def test_trim_keeps_pairing_marks_and_window(trim_settings):
    big = _big("陈旧结果")
    msgs = _convo(big)
    out = trimming.trim_for_request(msgs)

    # 配对铁律：绝不增删消息，tool call/result 一一对应
    assert len(out) == len(msgs)
    assert [m.tool_call_id for m in out if isinstance(m, ToolMessage)] == \
        [m.tool_call_id for m in msgs if isinstance(m, ToolMessage)]

    # 陈旧 tool result（窗口外 c1/c2）：首尾保留 + 裁剪标记带原文长度
    for i in (3, 5):
        body = msgs[i].content
        cut = out[i].content
        assert cut.startswith(body[:20]) and cut.endswith(body[-20:])
        assert "已按上下文预算裁剪" in cut
        assert str(len(body)) in cut
        assert len(cut) < len(body)

    # 保留：SystemMessage 与首条 HumanMessage（任务约束）全文不动
    assert out[0].content == msgs[0].content
    assert out[1].content == msgs[1].content

    # 保留：最近 N 轮窗口内（c3/c4）不被裁剪；最后一条 tool result 永不裁
    assert out[8].content == msgs[8].content
    assert out[12].content == msgs[12].content

    # checkpoint 原对象只读：入参消息 content 不变（model_copy，非原地替换）
    assert msgs[3].content == big and msgs[5].content == big

    # token 明显下降
    assert trimming.estimate_tokens(out) < trimming.estimate_tokens(msgs) * 0.5


def test_trim_noop_within_budget(trim_settings):
    msgs = _convo("短结果", small="另一个短结果")
    out = trimming.trim_for_request(msgs)
    assert len(out) == len(msgs)
    assert [m.content for m in out] == [m.content for m in msgs]


def test_trim_disabled_returns_untouched(monkeypatch):
    monkeypatch.setattr(settings, "agent_trim_enabled", False)
    msgs = _convo(_big("x"))
    out = trimming.trim_for_request(msgs)
    assert [m.content for m in out] == [m.content for m in msgs]


def test_trim_shrinks_window_tools_when_stale_cheap(trim_settings):
    """陈旧正文过小（裁剪无收益跳过）而窗口内巨大：第二优先收缩窗口内旧 tool，
    最后一条 tool result 永不裁。"""
    msgs = [
        SystemMessage(content="你是项目助手。"),
        HumanMessage(content="任务约束：只输出要点。"),
        _ai_call("w1"),
        _result("w1", "太小不裁"),           # len=4 ≤ head+tail+MIN_SAVE → 跳过
        HumanMessage(content="第二轮"),
        _ai_call("w2"),
        _result("w2", _big("窗口内旧")),
        _ai_call("w3"),
        _result("w3", _big("窗口内新")),     # 最后一条 → 永不裁
    ]
    out = trimming.trim_for_request(msgs)
    assert len(out) == len(msgs)
    assert out[3].content == "太小不裁"
    assert "已按上下文预算裁剪" in out[6].content
    assert out[8].content == msgs[8].content


def test_trim_emits_compaction_span(trim_settings, otel_mem):
    msgs = _convo(_big("陈旧结果"))
    trimming.trim_for_request(msgs)
    assert otel._provider is not None
    otel._provider.force_flush(timeout_millis=5000)
    spans = [s for s in otel_mem.get_finished_spans() if s.name == "context.compaction"]
    assert len(spans) == 1
    a = spans[0].attributes
    assert a["trimmed_messages"] == 2
    assert a["saved_tokens"] > 0
    assert a["budget_tokens"] == 300
