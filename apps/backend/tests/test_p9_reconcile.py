"""P9 §3.2 落痕对账守护测试：三签名检测 + 幂等留痕 + 分桶计数 + 人工出口。

前置回归：契约 TurnStart 已含 run_id/epoch 可选字段（P9 漏项修复）——
store.append 落 turn/start 带 run_id/epoch 不再被 extra='forbid' 拒绝。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.persistence.session import SessionLocal
from app.platform.daemons import run_reconcile
from app.platform.events.store import store


@pytest.fixture(autouse=True)
def _reset_reconcile_state():
    """模块级分桶/快照在用例间隔离。"""
    run_reconcile._buckets.update({s: 0 for s in run_reconcile.SIGNATURES})
    run_reconcile._owner_snapshot.clear()
    yield
    run_reconcile._buckets.update({s: 0 for s in run_reconcile.SIGNATURES})
    run_reconcile._owner_snapshot.clear()


def _append_turn_start(sid: str, turn: int, run_id: str, epoch: int) -> None:
    store.append(sid, "turn/start",
                 {"turn": turn, "version": 1, "run_id": run_id, "epoch": epoch}, turn=turn)


def _insert_run(sid: str, *, status: str = "running", owner: str = "hostA:1",
                epoch: int = 1, expires_in: float = 3600.0) -> str:
    """直插 RunRecord 行（raw SQL，绕过 claim 条件便于构造异常态）。"""
    rid = str(uuid.uuid4())
    exp = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    with SessionLocal() as db:
        db.execute(text(
            "INSERT INTO platform_session_runs "
            "(session_id, run_id, status, owner, epoch, expires_at, heartbeat_at, detail, "
            " created_at, updated_at) VALUES "
            "(:sid, :rid, :status, :owner, :epoch, :exp, now(), '{}'::jsonb, now(), now()) "
            "ON CONFLICT (session_id) DO UPDATE SET run_id = EXCLUDED.run_id, status = EXCLUDED.status, "
            " owner = EXCLUDED.owner, epoch = EXCLUDED.epoch, expires_at = EXCLUDED.expires_at, updated_at = now()"
        ), {"sid": uuid.UUID(sid), "rid": uuid.UUID(rid), "status": status,
            "owner": owner, "epoch": epoch, "exp": exp})
        db.commit()
    return rid


def _run_row(sid: str):
    with SessionLocal() as db:
        row = db.execute(text(
            "SELECT status, owner, epoch, detail FROM platform_session_runs WHERE session_id = :sid"
        ), {"sid": uuid.UUID(sid)}).first()
        db.commit()
    return row


def _scan() -> dict:
    with SessionLocal() as db:
        return run_reconcile.reconcile_once(db)


def test_contract_turn_start_carries_run_footprint(mk_session):
    """契约回归：turn/start 携带 run_id/epoch 落库（§3.2 数据源，P9 漏项修复）。"""
    sid = mk_session()
    _append_turn_start(sid, 1, str(uuid.uuid4()), 1)
    with SessionLocal() as db:
        row = db.execute(text(
            "SELECT data->>'run_id' AS rid, data->>'epoch' AS ep FROM platform_session_events "
            "WHERE session_id = :sid AND type = 'turn/start'"
        ), {"sid": uuid.UUID(sid)}).first()
        db.commit()
    assert row.rid and int(row.ep) == 1


def test_detect_dup_turn_start_idempotent(mk_session):
    """S1：同 turn 双 run_id 报一次；重扫不重报（幂等键）。"""
    sid = mk_session()
    _append_turn_start(sid, 1, str(uuid.uuid4()), 1)
    _append_turn_start(sid, 1, str(uuid.uuid4()), 2)  # 双开：同 turn 两个 run_id
    assert _scan()["dup_turn_start"] == 1
    assert _scan()["dup_turn_start"] == 0  # 幂等：同签名不重报
    assert run_reconcile.buckets_snapshot()["dup_turn_start"] == 1
    items = run_reconcile.list_anomalies(SessionLocal(), session_id=sid)
    assert len(items) == 1 and items[0]["signature"] == "dup_turn_start"
    assert items[0]["handled"] is False


def test_detect_epoch_regression(mk_session):
    """S2（签名表）：晚写入的 turn/start epoch 更低 = 自弃失效。"""
    sid = mk_session()
    _append_turn_start(sid, 1, str(uuid.uuid4()), 3)
    _append_turn_start(sid, 2, str(uuid.uuid4()), 2)  # 倒退
    assert _scan()["epoch_regression"] == 1


def test_owner_flip_positive_and_negatives(mk_session):
    """S3：租约未到期被换 owner → 报；waiting 合法翻转 / 过期后翻转 → 不报。"""
    sid = mk_session()
    # 阳性：A 租约远未到期（双活证据），被 B 替换且仍 running
    _insert_run(sid, owner="hostA:1", expires_in=3600.0)
    _scan()  # 建基线
    _insert_run(sid, owner="hostB:2", epoch=2, expires_in=3600.0)  # 强行翻转
    assert _scan()["owner_flip"] == 1

    # 阴性 1：waiting_interrupt 的跨副本 resume 是合法翻转（本轮无新增）
    sid2 = mk_session()
    _insert_run(sid2, status="waiting_interrupt", owner="hostA:1", expires_in=3600.0)
    _scan()
    _insert_run(sid2, owner="hostB:2", epoch=2, expires_in=3600.0)  # resume 翻转成 running
    assert _scan()["owner_flip"] == 0  # 合法翻转不新增

    # 阴性 2：上轮租约早已过期 → 合法崩溃接管，不报
    sid3 = mk_session()
    _insert_run(sid3, owner="hostA:1", expires_in=-600.0)
    _scan()
    _insert_run(sid3, owner="hostB:2", epoch=2, expires_in=3600.0)
    assert _scan()["owner_flip"] == 0  # 过期接管不新增


def test_actions_idempotent_and_effective(mk_session):
    """人工出口：ack 仅留痕；force_error 置错；reclaim 换代次；重复动作 duplicate。"""
    sid = mk_session()
    _append_turn_start(sid, 1, str(uuid.uuid4()), 1)
    _append_turn_start(sid, 1, str(uuid.uuid4()), 2)
    _scan()
    anomaly = run_reconcile.list_anomalies(SessionLocal(), session_id=sid)[0]
    aid = anomaly["log_id"]

    with SessionLocal() as db:
        # ack：确认误报，仅留痕
        out = run_reconcile.apply_action(db, sid, "ack", aid, actor="admin1")
        assert out == {"action": "ack", "duplicate": False}
        assert run_reconcile.apply_action(db, sid, "ack", aid, actor="admin1")["duplicate"] is True

        # force_error：活跃行置 error；终态后再次 force 不改（changed=0），reclaim 幂等独立
        _insert_run(sid, owner="hostA:1", epoch=5, expires_in=60.0)
        out = run_reconcile.apply_action(db, sid, "force_error", aid, actor="admin1")
        assert out["changed"] == 1 and _run_row(sid).status == "error"
        assert run_reconcile.apply_action(db, sid, "force_error", aid, actor="admin2")["duplicate"] is True

        # reclaim：换代次重置（epoch+1、新 run_id、error）——旧代次写入立即失效
        _insert_run(sid, owner="hostA:1", epoch=9, expires_in=60.0)
        before = _run_row(sid)
        out = run_reconcile.apply_action(db, sid, "reclaim", aid, actor="admin1")
        after = _run_row(sid)
        assert out["changed"] == 1
        assert after.status == "error" and after.epoch == before.epoch + 1
        assert after.detail["reason"] == "manual_reclaim"

        # 异常记录不存在 / 跨会话引用 → LookupError
        with pytest.raises(LookupError):
            run_reconcile.apply_action(db, sid, "ack", 999999999, actor="admin1")

    # 查询侧：handled 状态与最近动作可见
    items = run_reconcile.list_anomalies(SessionLocal(), session_id=sid)
    assert items[0]["handled"] is True
    assert items[0]["last_action"]["action"] == "reclaim"
