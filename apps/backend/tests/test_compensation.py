"""P6 改造回归：补偿闭环守护（业务落库失败的幂等重放）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.persistence.models import PlatformCompensation
from app.platform.agent import component_handlers
from app.platform.daemons.compensation import _due_at, replay_once


@pytest.fixture
def comp_row(db, mk_session):
    """pending 补偿记录 factory：stale=True 时把 created_at 拉早使退避窗口已过。"""
    created: list[PlatformCompensation] = []

    def _make(kind="pytest-kind", retry_count=0, stale=True):
        row = PlatformCompensation(
            session_id=uuid.UUID(mk_session()),
            event_seq=1, plugin_name=kind, action="replay_submit",
            payload={"component_id": "c1", "props": {"x": 1}, "values": {}},
            retry_count=retry_count,
        )
        if stale:
            row.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.add(row)
        db.commit()
        created.append(row)
        return row

    yield _make
    for r in created:
        db.expire(r)  # 回卷后重新读取真实状态


@pytest.fixture(autouse=True)
def _clean_compensations(db):
    """replay_once 全表扫描：每个用例前清空补偿表，防跨用例残留污染计数断言。"""
    from sqlalchemy import delete

    db.execute(delete(PlatformCompensation))
    db.commit()
    yield
    db.execute(delete(PlatformCompensation))
    db.commit()


@pytest.fixture(autouse=True)
def _handlers_cleanup():
    before = dict(component_handlers.HANDLERS)
    yield
    component_handlers.HANDLERS.clear()
    component_handlers.HANDLERS.update(before)


def test_due_backoff_window():
    now = datetime.now(timezone.utc)
    row = PlatformCompensation(
        session_id=uuid.uuid4(), event_seq=1,
        plugin_name="k", action="replay_submit", payload={},
        created_at=now,
    )
    # 第 0 次重放：2^0=1 分钟后到期；第 2 次：4 分钟后
    assert _due_at(row) == now + timedelta(minutes=1)
    row.retry_count = 2
    assert _due_at(row) == now + timedelta(minutes=4)
    row.retry_count = 99  # 封顶 2^6
    assert _due_at(row) == now + timedelta(minutes=64)


def test_replay_success_marks_done(db, comp_row):
    calls = []

    def _handler(db, *, session_id, user_id, props, values):
        calls.append((session_id, user_id, props, values))
        return "已处理", [("pytest/done", {})]

    component_handlers.register("pytest-kind", _handler)
    row = comp_row()  # stale=True：窗口已过

    n = replay_once(db, owner="pytest")
    assert n == 1
    db.refresh(row)
    assert row.status == "done"
    assert row.retry_count == 0
    assert calls and calls[0][1]  # user_id 来自会话头


def test_replay_within_backoff_window_skipped(db, comp_row):
    component_handlers.register(
        "pytest-kind", lambda db, **kw: (_ for _ in ()).throw(AssertionError("窗口内不应重放"))
    )
    row = comp_row(stale=False)
    row.created_at = datetime.now(timezone.utc) + timedelta(minutes=30)  # 窗口未到
    db.commit()
    assert replay_once(db, owner="pytest") == 0
    db.refresh(row)
    assert row.status == "pending"


def test_replay_failure_increments_retry_then_failed(db, comp_row):
    def _boom(db, **kw):
        raise RuntimeError("下游挂了")

    component_handlers.register("pytest-kind", _boom)
    row = comp_row(retry_count=4)  # 已重试 4 次，本轮是第 5 次 → 达上限

    replay_once(db, owner="pytest")
    db.refresh(row)
    assert row.status == "failed"
    assert row.retry_count == 5

    # 未达上限：留 pending 等下一轮
    row2 = comp_row(retry_count=0)
    replay_once(db, owner="pytest")
    db.refresh(row2)
    assert row2.status == "pending"
    assert row2.retry_count == 1


def test_missing_handler_marks_failed(db, comp_row):
    component_handlers.HANDLERS.pop("pytest-orphan", None)
    row = comp_row(kind="pytest-orphan")

    replay_once(db, owner="pytest")
    db.refresh(row)
    assert row.status == "failed"


# ---------- 复核证据（P6 补偿幂等 / 在途未知） ----------


def test_idem_key_deterministic_and_passed_through(db, comp_row):
    """复核证据：确定性幂等键 {kind}:{session_id}:{seq} 透传业务 handler。

    声明 idem_key 形参的处理器收到该键，业务落库可用它做唯一约束/upsert；
    同一补偿无论重放多少次，业务侧都能凭该键识别重复提交。
    """
    seen = {}

    def _handler(db, *, session_id, user_id, props, values, idem_key):
        seen["idem_key"] = idem_key
        return "已处理", []

    component_handlers.register("pytest-kind", _handler)
    row = comp_row()

    assert replay_once(db, owner="pytest") == 1
    expected = f"pytest-kind:{row.session_id}:{row.event_seq}"
    assert row.idem_key == expected  # 确定性：仅由 kind/session/seq 决定
    assert seen["idem_key"] == expected  # 条件透传（handler 声明了形参）
    db.refresh(row)
    assert row.status == "done"


def test_crash_after_business_write_no_duplicate_replay(db, comp_row):
    """复核证据：崩溃窗口——「业务写成功、补偿记录未标记」后重放，业务侧零重复副作用。

    残留 replaying（重放中崩溃）→ 下一轮升级 needs_manual_review，绝不盲目重放。
    """
    calls = []

    def _handler(db, **kw):
        calls.append(kw)
        return "已处理", []

    component_handlers.register("pytest-kind", _handler)
    row = comp_row()
    # 模拟崩溃窗口：上一轮 replay 已写业务，进程在标记 done 前崩溃 → 残留 replaying
    row.status = "replaying"
    db.commit()

    assert replay_once(db, owner="pytest") == 1
    db.refresh(row)
    assert row.status == "needs_manual_review"
    assert calls == []  # 业务侧无重复副作用（人工确认前不再触碰业务库）


def test_timeout_marks_needs_manual_review_no_blind_retry(db, comp_row):
    """复核证据：在途未知——超时类错误不自动重放，转 needs_manual_review。"""
    calls = []

    def _slow(db, **kw):
        calls.append(kw)
        raise TimeoutError("下游超时")

    component_handlers.register("pytest-kind", _slow)
    row = comp_row()

    assert replay_once(db, owner="pytest") == 1
    db.refresh(row)
    assert row.status == "needs_manual_review"
    assert row.retry_count == 0  # 结果未知 ≠ 可计数的失败
    assert len(calls) == 1

    # 人工确认前禁止自动重试：needs_manual_review 不在扫描范围
    assert replay_once(db, owner="pytest") == 0
    assert len(calls) == 1
