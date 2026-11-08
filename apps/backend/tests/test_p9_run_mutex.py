"""P9 门禁 A/D 组：RunRecord claim CAS 互斥正确性（docs/p9-design.md §4）。

- T1：同会话并发 claim（屏障同步）恰好 1 成功，其余 409——闭合「先查后登」TOCTOU；
- T2/T3：跨副本语义用两个 owner 身份对同一 DB 行竞争（claim 是 DB 原子语句，
  进程边界不改变行级语义：恰好一胜一拒、事件单条由翻转唯一性保证）；
- T7b：副本重启（内存空）后同会话立即重入 → 启动对账置 error → DB CAS 放行。

测试直连 dev 库（conftest 统一约束），用独立 session_id 隔离，行随终态保留。
"""
from __future__ import annotations

import os
import socket
import threading
import uuid

import pytest
from sqlalchemy import text

from app.platform.agent import run_record
from app.persistence.session import engine

ME = run_record.owner_id()
OTHER = "gate-other-replica:1"


def _expire_lease(session_id: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(
            "UPDATE platform_session_runs SET expires_at = now() - interval '1 second' "
            "WHERE session_id = :sid"), {"sid": uuid.UUID(session_id)})
        conn.commit()


def _set_status(session_id: str, status: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(
            "UPDATE platform_session_runs SET status = :st WHERE session_id = :sid"),
            {"st": status, "sid": uuid.UUID(session_id)})
        conn.commit()


# ---------- T1：同会话并发 claim，恰好 1 成功 ----------

def test_t1_concurrent_claim_exactly_one_winner(mk_session):
    sid = mk_session()
    n = 8
    barrier = threading.Barrier(n)
    results: list[tuple[str, object]] = []
    lock = threading.Lock()

    def _worker(i):
        barrier.wait()
        try:
            t = run_record.claim_start(sid, turn=1, owner=f"t1-worker-{i}")
            r = ("ok", t)
        except run_record.ClaimRejected as exc:
            r = ("rejected", exc.reason)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = [r for r in results if r[0] == "ok"]
    rej = [r for r in results if r[0] == "rejected"]
    assert len(ok) == 1, f"恰好 1 成功，实际 {len(ok)}：{results}"
    assert len(rej) == n - 1
    assert all(r[1] == "busy" for r in rej), f"败者一律 busy（租约有效）：{rej}"
    row = run_record.read_run_row(sid)
    assert row.status == "running"
    assert row.epoch == 1  # 首次 claim epoch=1
    assert str(row.run_id) == ok[0][1].run_id


# ---------- claim 条件矩阵 ----------

def test_claim_start_blocked_by_waiting_interrupt(mk_session):
    """waiting_interrupt 且租约有效 → 不可开新轮（busy，行为对齐原 run_busy）。"""
    sid = mk_session()
    run_record.claim_start(sid, turn=1, owner=ME)
    assert run_record.mark(sid, run_record.read_run_row(sid).run_id, "waiting_interrupt",
                           interrupt_id="i1", component_id="c1")
    with pytest.raises(run_record.ClaimRejected) as ei:
        run_record.claim_start(sid, turn=2, owner=ME)
    assert ei.value.reason == "busy"


def test_claim_start_after_terminal_immediate_and_epoch_grows(mk_session):
    """终态不删行：下一轮 claim 原位覆盖，epoch 单调 +1。"""
    sid = mk_session()
    t1 = run_record.claim_start(sid, turn=1, owner=ME)
    _set_status(sid, "done")
    t2 = run_record.claim_start(sid, turn=2, owner=ME)
    assert t2.epoch == t1.epoch + 1
    assert t2.run_id != t1.run_id
    assert run_record.read_run_row(sid).turn == 2


def test_claim_start_takeover_on_expired_lease(mk_session):
    """running 且租约过期（崩溃判定）→ 异副本可接管，epoch+1（T7 恢复驱动）。"""
    sid = mk_session()
    t1 = run_record.claim_start(sid, turn=1, owner=ME)
    _expire_lease(sid)
    t2 = run_record.claim_start(sid, turn=2, owner=OTHER)
    assert t2.epoch == 2
    row = run_record.read_run_row(sid)
    assert row.owner == OTHER and str(row.run_id) == t2.run_id


# ---------- 409 reason 分类（P9 §3.1） ----------

def test_classify_busy_vs_taken_over(mk_session):
    sid = mk_session()
    run_record.claim_start(sid, turn=1, owner=OTHER)
    assert run_record._classify(sid) == "busy"  # 租约有效 → 可退避重试
    _expire_lease(sid)
    assert run_record._classify(sid) == "taken_over"  # 已过期 → 刷新重拉，不重试


# ---------- T3：resume 翻转恰好一胜一拒 ----------

def test_t3_resume_flip_single_winner(mk_session):
    sid = mk_session()
    run_record.claim_start(sid, turn=1, owner=ME)
    assert run_record.mark(sid, run_record.read_run_row(sid).run_id, "waiting_interrupt")

    # 异副本 resume 等待中的会话：翻转成功者获得执行权（waiting 声明不被执行占用）
    t_b = run_record.claim_resume(sid, owner=OTHER)
    assert t_b.epoch == 2
    row = run_record.read_run_row(sid)
    assert row.status == "running" and row.owner == OTHER

    # 原副本再 resume → 拒绝（翻转已完成，busy：另一 run 活着）
    with pytest.raises(run_record.ClaimRejected) as ei:
        run_record.claim_resume(sid, owner=ME)
    assert ei.value.reason == "busy"

    # 双副本同时抢 resume 同一 waiting 行：恰好一胜
    sid2 = mk_session()
    run_record.claim_start(sid2, turn=1, owner=ME)
    assert run_record.mark(sid2, run_record.read_run_row(sid2).run_id, "waiting_interrupt")
    barrier = threading.Barrier(2)
    out: list[str] = []
    lock = threading.Lock()

    def _resume(owner):
        barrier.wait()
        try:
            run_record.claim_resume(sid2, owner=owner)
            r = "win"
        except run_record.ClaimRejected:
            r = "lose"
        with lock:
            out.append(r)

    ts = [threading.Thread(target=_resume, args=(o,)) for o in (ME, OTHER)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == ["lose", "win"], f"恰好一胜一拒：{out}"


def test_resume_takeover_crashed_running(mk_session):
    """running 且租约过期 → resume 接管（崩溃孤儿中断恢复，T7 链路）。"""
    sid = mk_session()
    run_record.claim_start(sid, turn=1, owner=OTHER)
    _expire_lease(sid)
    t = run_record.claim_resume(sid, owner=ME)
    assert t.epoch == 2
    assert run_record.read_run_row(sid).owner == ME


# ---------- fencing：接管后旧代次写不进 ----------

def test_fencing_old_run_id_cannot_renew_or_mark(mk_session):
    sid = mk_session()
    t1 = run_record.claim_start(sid, turn=1, owner=ME)
    assert run_record.renew(sid, t1.run_id) is True
    _expire_lease(sid)
    t2 = run_record.claim_start(sid, turn=2, owner=OTHER)  # 接管
    assert run_record.renew(sid, t1.run_id) is False  # 旧代次续期失败 → 自弃信号
    assert run_record.mark(sid, t1.run_id, "done") is False  # 旧代次落不了终态
    assert run_record.mark(sid, t2.run_id, "done") is True
    assert run_record.read_run_row(sid).status == "done"


# ---------- T7b：重启（内存空）后立即重入 → 启动对账置 error → claim 放行 ----------

def test_t7b_startup_reconcile_unblocks_reentry(mk_session):
    sid = mk_session()
    dead_pid = os.getpid() + 98765  # 同主机已死进程的 pid（内存已空）
    run_record.claim_start(sid, turn=1, owner=f"{socket.gethostname()}:{dead_pid}")
    n = run_record.reconcile_stale_local_runs()
    assert n >= 1
    assert run_record.read_run_row(sid).status == "error"
    t = run_record.claim_start(sid, turn=2, owner=ME)  # 立即重入不被误拒
    assert t.epoch == 2


def test_reconcile_leaves_foreign_rows_alone(mk_session):
    sid = mk_session()
    run_record.claim_start(sid, turn=1, owner=OTHER)  # 他机活跃副本
    run_record.reconcile_stale_local_runs()
    assert run_record.read_run_row(sid).status == "running"
