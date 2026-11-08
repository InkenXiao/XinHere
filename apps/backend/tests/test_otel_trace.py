"""可观测性最小闭环验收：agent.run → tool.execute(delegate_task) → subagent.run →
子图 llm.call / tool.execute 完整父子链；token_usage / tool_tier / step_index /
subagent_depth 属性；补偿重放独立 span（compensation_kind / retry_count / result）。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")

from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from app.persistence.models import PlatformCompensation  # noqa: E402
from app.platform.agent import otel  # noqa: E402
from app.platform.agent.component_handlers import HANDLERS  # noqa: E402
from app.platform.daemons import compensation  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_compensations(db):
    """replay_once 全表扫描：用例前后清空补偿表，防 dev 库残留行污染计数断言。"""
    from sqlalchemy import delete

    db.execute(delete(PlatformCompensation))
    db.commit()
    yield
    db.execute(delete(PlatformCompensation))
    db.commit()


@pytest.fixture(autouse=True)
def _handlers_cleanup():
    before = dict(HANDLERS)
    yield
    HANDLERS.clear()
    HANDLERS.update(before)


@pytest.fixture
def trace():
    otel.reset_for_tests()
    exporter = InMemorySpanExporter()
    otel.setup_otel(exporter=exporter)
    yield exporter
    otel.shutdown_otel()
    otel.reset_for_tests()


def _flush(exporter) -> list:
    assert otel._provider is not None
    otel._provider.force_flush(timeout_millis=5000)
    return list(exporter.get_finished_spans())


def _named(spans, name):
    return [s for s in spans if s.name == name]


def test_agent_run_to_subgraph_chain_and_attrs(trace):
    with otel.run_span(session_id="s-otel", turn=3, user_id="u1", request_id="r1"):
        parent_ctx = otel.get_current_context()
        # 工具线程模拟：显式 parent_context 挂靠 agent.run（contextvars 跨线程不传播）
        with otel.tool_span(session_id="s-otel", tool_name="delegate_task", tool_tier="read",
                            call_id="c1", parent_context=parent_ctx,
                            step_index=2, subagent_depth=0):
            with otel.subagent_span(session_id="s-otel", name="research",
                                    provider="general", depth=1):
                # 子图内部工具（跨线程语义：显式携带 depth=1）
                with otel.tool_span(session_id="s-otel", tool_name="search_knowledge",
                                    tool_tier="read", call_id="c2",
                                    step_index=1, subagent_depth=1):
                    pass
                # 子图 LLM：自然挂靠 subagent.run，token usage 显式写入
                with otel.llm_span(model="test-model", channel="",
                                   message_count=5, prompt_chars=120) as llm_obs:
                    llm_obs.set_usage(
                        {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})
        # 子代理退出后 depth 恢复：父图后续 llm span 不误标子深度
        with otel.llm_span(model="test-model", channel="", message_count=2, prompt_chars=10):
            pass

    spans = _flush(trace)
    runs = _named(spans, "agent.run")
    tools = _named(spans, "tool.execute")
    subs = _named(spans, "subagent.run")
    llms = _named(spans, "llm.call")
    assert len(runs) == 1 and len(tools) == 2 and len(subs) == 1 and len(llms) == 2

    root = runs[0]
    assert root.parent is None
    assert root.attributes["session_id"] == "s-otel" and root.attributes["turn"] == 3

    delegate = next(s for s in tools if s.attributes["tool_name"] == "delegate_task")
    assert delegate.parent.span_id == root.context.span_id  # 跨线程显式父链
    assert delegate.attributes["tool_tier"] == "read"
    assert delegate.attributes["step_index"] == 2
    assert delegate.attributes["subagent_depth"] == 0
    assert delegate.attributes["tool_call_id"] == "c1"

    sub = subs[0]
    assert sub.parent.span_id == delegate.context.span_id  # delegate_task → subagent.run
    assert sub.attributes["subagent_name"] == "research"
    assert sub.attributes["subagent_depth"] == 1

    child_tool = next(s for s in tools if s.attributes["tool_name"] == "search_knowledge")
    assert child_tool.parent.span_id == sub.context.span_id  # 子图工具挂靠 subagent.run
    assert child_tool.attributes["subagent_depth"] == 1

    child_llm = next(s for s in llms if s.attributes["subagent_depth"] == 1)
    assert child_llm.parent.span_id == sub.context.span_id  # 子图 LLM 挂靠 subagent.run
    assert child_llm.attributes["session_id"] == "s-otel"
    usage = json.loads(child_llm.attributes["token_usage"])
    assert usage == {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}
    assert child_llm.attributes["llm.usage.input_tokens"] == 120
    assert child_llm.attributes["llm.usage.output_tokens"] == 30
    assert child_llm.attributes["llm.usage.total_tokens"] == 150

    after_llm = next(s for s in llms if s.attributes["subagent_depth"] == 0)
    assert after_llm.parent.span_id == root.context.span_id  # 退出子代理后挂回 agent.run


def test_compensation_replay_span(trace, db, mk_session):
    """验收：补偿重放独立 span，带 compensation_kind / retry_count / result，可观测。"""
    sid = mk_session()
    calls: list = []

    def _handler(db, *, session_id, user_id, props, values, idem_key=None):
        calls.append((session_id, idem_key))
        return "重放成功", {"ok": True}

    HANDLERS["otel_comp_test"] = _handler
    try:
        row = PlatformCompensation(
            session_id=uuid.UUID(sid), event_seq=7, plugin_name="otel_comp_test",
            action="replay_submit",
            payload={"component_id": "otel_comp_test-x", "props": {}, "values": {}},
            status="pending", retry_count=1,
        )
        db.add(row)
        db.commit()
        # 把退避窗口拨到已到期（retry_count=1 → due = created_at + 2min）
        row.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        db.commit()

        assert compensation.replay_once(db, owner="pytest") == 1
        assert row.status == "done"
        assert calls == [(sid, row.idem_key)]  # 业务处理器收到确定性幂等键

        spans = _flush(trace)
        comp = _named(spans, "compensation.replay")
        assert len(comp) == 1
        a = comp[0].attributes
        assert a["compensation_kind"] == "otel_comp_test"
        assert a["retry_count"] == 1
        assert a["result"] == "done"
        assert a["session_id"] == sid
        assert a["idem_key"] == row.idem_key
    finally:
        HANDLERS.pop("otel_comp_test", None)
