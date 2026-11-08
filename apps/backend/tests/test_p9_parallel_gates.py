"""P9 门禁 B/C 组：真并行、连接池、自弃与回滚演练（docs/p9-design.md §4）。

- T4：N 会话并行完整轮——主断言（确定性）DB 采样 running ≥ 2 + trace 扫掠执行区间
  重叠 ≥ 2（「无全局串行点」直接证据）；辅断言 mock 固定延迟下并发总时长
  < 串行 × 0.6；
- T5：真 langgraph（真实池化 PostgresSaver）长对话：多轮 + interrupt + resume +
  get_state，状态与事件日志全程一致；跨连接 checkpoint 连续性（池化正确性）；
- T5b：池耗尽快速失败（PoolExhausted → 503 路径），借空时无 RunRecord 残留；
- T6：接管后旧副本自弃——中止后无任何 checkpoint/事件写入，暴露窗口
  ≤ 单个 item 时长 + 续期间隔（受控 sleep 量化，§2.2 口径），行归接管者；
- T9：回滚演练——flag off 恢复全局锁（执行区间不重叠）、saver 保持池化、
  启动对账置 error 后旧路径立即可重入。

executor 级测试策略：fresh AgentExecutor + monkeypatch settings 后 setup()；
EventTranslator 换 no-op 桩；FakeGraph 脚本化流（可控 sleep 量化窗口），
trace 记录进出执行区间供并行度断言；真 langgraph 仅用于 T5。
"""
from __future__ import annotations

import os
import queue
import socket
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.core import errors
from app.platform.agent import run_record
from app.platform.agent import executor as executor_mod
from app.platform.agent.executor import AgentExecutor, PoolExhausted
from app.persistence.models import SysUser
from app.persistence.session import engine

ME = run_record.owner_id()


# ---------- 桩与辅助 ----------

class _StubTranslator:
    """事件翻译桩：门禁关注 executor 编排与 DB 落痕，不关注 chunk 翻译。"""

    def __init__(self, _store, sid, turn):
        self.sid, self.turn = sid, turn

    def handle_chunk(self, chunk, meta):  # noqa: ARG002
        pass

    def handle_update(self, payload):  # noqa: ARG002
        pass

    def finalize(self):
        pass


class FakeGraph:
    """脚本化图：("sleep", 秒) 控速 / ("updates", payload) 产出 / ("set_interrupt", v) 挂起。

    sink 模拟「每步 checkpoint 写入」（T6 断言中止后停止增长）；trace 记录
    enter/exit 执行区间（T4/T9 并行度断言）。
    """

    def __init__(self, sid, script, sink, trace):
        self.sid, self.script = sid, list(script)
        self.sink, self.trace = sink, trace
        self.consumed = 0

    def stream(self, input_, config, stream_mode=None, subgraphs=False):
        self.trace.append(("enter", self.sid, time.monotonic()))

        def gen():
            try:
                for kind, val in self.script:
                    if kind == "sleep":
                        time.sleep(val)
                    elif kind == "updates":
                        self.consumed += 1
                        self.sink.append((self.sid, self.consumed, time.monotonic()))
                        yield ("updates", val)
                    elif kind == "set_interrupt":
                        yield ("updates", {"__interrupt__": True})
            finally:
                self.trace.append(("exit", self.sid, time.monotonic()))

        return gen()

    def get_state(self, config):
        tasks = []
        for kind, val in self.script:
            if kind == "set_interrupt":
                tasks.append(SimpleNamespace(interrupts=[SimpleNamespace(value=val)]))
        return SimpleNamespace(tasks=tasks)


def _user() -> SysUser:
    # 仅访问 user_id/username/xuanpu_user_id，无需落库
    return SysUser(user_id="pytest", username="pytest")


def _install_graphs(monkeypatch, graphs: dict):
    def _fake_build(self, tool_ctx, saver, *args, **kwargs):  # noqa: ARG001 - 类级替换需接 self
        return graphs[tool_ctx.session_id]

    monkeypatch.setattr(AgentExecutor, "_build_graph", _fake_build)


@pytest.fixture
def make_exec(monkeypatch):
    """构建 fresh AgentExecutor：先 monkeypatch settings 再 setup，结束关池连接。"""
    built: list[AgentExecutor] = []

    def _make(flag_on: bool, pool_size: int) -> AgentExecutor:
        monkeypatch.setattr(settings, "session_parallel_enabled", flag_on)
        monkeypatch.setattr(settings, "checkpoint_pool_size", pool_size)
        monkeypatch.setattr(executor_mod, "EventTranslator", _StubTranslator)
        ex = AgentExecutor()
        ex.setup()
        built.append(ex)
        return ex

    yield _make

    for ex in built:
        pool = ex._saver_pool
        if pool is None:
            continue
        while True:
            try:
                saver = pool.get_nowait()
            except queue.Empty:
                break
            conn = getattr(saver, "conn", None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass


def _wait_until(cond, timeout: float, msg: str = "条件未在时限内满足"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(msg)


def _expire_lease(session_id: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(
            "UPDATE platform_session_runs SET expires_at = now() - interval '1 second' "
            "WHERE session_id = :sid"), {"sid": uuid.UUID(session_id)})
        conn.commit()


def _event_types(session_id: str) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT type FROM platform_session_events WHERE session_id = :sid ORDER BY seq"),
            {"sid": uuid.UUID(session_id)}).all()
    return [r[0] for r in rows]


def _running_count(owner_prefix: str) -> int:
    with engine.connect() as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM platform_session_runs "
            "WHERE status = 'running' AND owner LIKE :p"), {"p": owner_prefix + "%"}).scalar()
    return int(n)


def _sweep_max_active(trace: list) -> int:
    """扫掠 trace：任一时刻最大并发执行数（「无全局串行点」的确定性证据）。"""
    evs = sorted((t, 1 if k == "enter" else -1) for k, _s, t in trace)
    cur = mx = 0
    for _t, d in evs:
        cur += d
        mx = max(mx, cur)
    return mx


def _exec_interval(trace: list, sid: str) -> tuple[float, float]:
    enters = [t for k, s, t in trace if k == "enter" and s == sid]
    exits = [t for k, s, t in trace if k == "exit" and s == sid]
    return (min(enters), max(exits))


# ---------- _raise_claim_http 映射（409 busy / 409 taken_over / 503） ----------

def test_raise_claim_http_mapping():
    from app.platform.api.sessions import _raise_claim_http

    with pytest.raises(errors.AppError) as ei:
        _raise_claim_http(PoolExhausted())
    assert ei.value.code == "RUN_POOL_BUSY" and ei.value.status == 503

    with pytest.raises(errors.AppError) as ei:
        _raise_claim_http(run_record.ClaimRejected("busy"))
    assert ei.value.code == "RUN_BUSY" and ei.value.status == 409

    with pytest.raises(errors.AppError) as ei:
        _raise_claim_http(run_record.ClaimRejected("taken_over"))
    assert ei.value.code == "RUN_TAKEN_OVER" and ei.value.status == 409

    with pytest.raises(ValueError):
        _raise_claim_http(ValueError("透传未知异常"))


# ---------- T4：N 会话并行完整轮（真并行证据） ----------

def test_t4_parallel_runs_overlap_and_faster(monkeypatch, mk_session, make_exec):
    ex = make_exec(flag_on=True, pool_size=4)
    sids = [mk_session() for _ in range(4)]
    per_run = 0.4  # 每轮固定延迟 2 × 0.2s（mock，无网络抖动）
    graphs: dict = {}
    sink: list = []
    trace: list = []
    _install_graphs(monkeypatch, graphs)
    for i, sid in enumerate(sids):
        script = [("sleep", 0.2), ("updates", {"n": 1}), ("sleep", 0.2), ("updates", {"n": 2})]
        graphs[sid] = FakeGraph(sid, script, sink, trace)
        saver = ex.acquire_run_slot()
        ticket = run_record.claim_start(sid, turn=1, owner=f"t4-{i}")
        ex.start_turn(sid, _user(), "hi", f"t4-req-{i}", ticket=ticket, saver=saver, turn=1)

    t0 = time.monotonic()
    max_db_running = 0
    deadline = t0 + 15
    while time.monotonic() < deadline:  # 执行窗内边采样边等完成（完成后行已 done，不能后补）
        max_db_running = max(max_db_running, _running_count("t4-"))
        if all(ex.get_run(s) is None for s in sids):
            break
        time.sleep(0.03)
    wall = time.monotonic() - t0
    assert all(ex.get_run(s) is None for s in sids), "4 轮应全部完成"

    # 主断言（确定性）：执行窗口内 DB 存在时刻 running ≥ 2；trace 扫掠并发 ≥ 2
    assert max_db_running >= 2, f"DB 采样 running 峰值 {max_db_running}"
    assert _sweep_max_active(trace) >= 2, "执行区间应重叠（无全局串行点）"
    # 辅断言：并发总时长 < 串行总时长 × 0.6（串行 = 4 × 0.4s）
    assert wall < 0.6 * len(sids) * per_run, f"并发 wall {wall:.2f}s 应 < {0.6 * len(sids) * per_run:.2f}s"
    # 每轮收尾：turn/end completed + RunRecord done
    for sid in sids:
        assert _event_types(sid) == ["turn/end"]
        assert run_record.read_run_row(sid).status == "done"


# ---------- T5：真实池化 checkpointer 的长对话（多轮 + interrupt + resume + get_state） ----------

def test_t5_pool_real_graph_multiturn_interrupt_resume(monkeypatch, mk_session, make_exec):
    ex = make_exec(flag_on=True, pool_size=2)
    sid = mk_session()

    from typing_extensions import TypedDict

    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt as lg_interrupt

    class GState(TypedDict, total=False):
        messages: list
        steps: list

    made: dict = {}

    def _build(self, tool_ctx, saver, *args, **kwargs):  # noqa: ARG002 - 类级替换需接 self
        with_interrupt = tool_ctx.turn == 1  # 仅首轮挂起，制造 interrupt → resume 链路

        g = StateGraph(GState)

        def node_a(state):
            time.sleep(0.05)
            return {"steps": list(state.get("steps", [])) + ["a"]}

        def node_b(state):
            if with_interrupt:
                # 载荷满足 component/request 契约（executor 仅补 interrupt_id）
                ack = lg_interrupt({"kind": "confirm", "component_id": f"c-{tool_ctx.session_id[:8]}",
                                    "kind_version": 1, "props": {"title": "确认"}, "version": 1})
                return {"steps": list(state.get("steps", [])) + [f"b:{ack.get('action')}"]}
            return {"steps": list(state.get("steps", [])) + ["b"]}

        g.add_node("a", node_a)
        g.add_node("b", node_b)
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", END)
        made["graph"] = g.compile(checkpointer=saver)  # 每次构建注入当次借出的池连接
        return made["graph"]

    monkeypatch.setattr(AgentExecutor, "_build_graph", _build)

    # 轮 1：interrupt 挂起（真实 checkpoint 写入池化连接）
    saver = ex.acquire_run_slot()
    t1 = run_record.claim_start(sid, turn=1)
    ex.start_turn(sid, _user(), "hi", "req-t5", ticket=t1, saver=saver, turn=1)
    def _waiting():
        h = ex.get_run(sid)
        return h if h is not None and h.status == "waiting_interrupt" else None

    handle = _wait_until(_waiting, 15, "轮 1 应挂起等待 interrupt")
    assert handle.pending_component_id == f"c-{sid[:8]}"
    assert run_record.read_run_row(sid).status == "waiting_interrupt"
    assert _event_types(sid) == ["component/request"]

    # resume：翻转执行权（本副本内换 run_id 代次），真实 Command(resume=...) 解挂
    ex.resume(sid, {"action": "confirm"})
    _wait_until(lambda: ex.get_run(sid) is None, 15, "resume 后应完成")
    assert run_record.read_run_row(sid).status == "done"
    assert _event_types(sid) == ["component/request", "turn/end"]

    # 轮 2：多轮续写（checkpoint 跨借还连接连续）
    saver2 = ex.acquire_run_slot()
    t2 = run_record.claim_start(sid, turn=2)
    ex.start_turn(sid, _user(), "hi", "req-t5-2", ticket=t2, saver=saver2, turn=2)
    _wait_until(lambda: ex.get_run(sid) is None, 15, "轮 2 应完成")
    assert run_record.read_run_row(sid).status == "done"
    assert t2.epoch == t1.epoch + 2  # resume 翻转自身占一次 epoch+1（§2.2 每次接管/翻转都换代）
    assert _event_types(sid) == ["component/request", "turn/end", "turn/end"]

    # get_state：线程状态与事件日志一致（interrupt 已解挂、两轮 steps 累计）
    state = made["graph"].get_state({"configurable": {"thread_id": sid}})
    assert state.values["steps"] == ["a", "b:confirm", "a", "b"]
    assert not any(getattr(t, "interrupts", None) for t in state.tasks)


def test_t5b_pool_exhausted_fast_fail_no_run_record_residue(monkeypatch, mk_session, make_exec):
    ex = make_exec(flag_on=True, pool_size=1)
    sid, sid2 = mk_session(), mk_session()
    graphs: dict = {}
    sink: list = []
    trace: list = []
    _install_graphs(monkeypatch, graphs)
    graphs[sid] = FakeGraph(sid, [("sleep", 1.0), ("updates", {"n": 1})], sink, trace)
    saver = ex.acquire_run_slot()
    ticket = run_record.claim_start(sid, turn=1, owner="t5b-a")
    ex.start_turn(sid, _user(), "hi", "req", ticket=ticket, saver=saver, turn=1)
    time.sleep(0.2)  # run 线程已持有唯一池连接

    with pytest.raises(PoolExhausted):
        ex.acquire_run_slot()
    # 借还次序（§2.3）：借空时未触碰 RunRecord → 第二会话无行残留
    assert run_record.read_run_row(sid2) is None

    _wait_until(lambda: ex.get_run(sid) is None, 10, "占池 run 应完成归还")
    assert run_record.read_run_row(sid).status == "done"
    saver2 = ex.acquire_run_slot()  # 归还后可再借
    assert ex._saver_pool.qsize() == 0
    ex.release_run_slot(saver2)
    assert ex._saver_pool.qsize() == 1


# ---------- T6：接管后旧副本自弃（窗口量化 + 零写入） ----------

def test_t6_self_abandon_after_takeover(monkeypatch, mk_session, make_exec):
    ex = make_exec(flag_on=True, pool_size=2)
    monkeypatch.setattr(settings, "run_lease_renew_seconds", 1)
    sid = mk_session()
    sink: list = []
    trace: list = []
    graphs: dict = {}
    script: list = []
    for i in range(5):  # 5 × 1.5s ≈ 7.5s 执行窗，足以覆盖接管检测
        script += [("sleep", 1.5), ("updates", {"n": i})]
    graph = FakeGraph(sid, script, sink, trace)
    graphs[sid] = graph
    _install_graphs(monkeypatch, graphs)
    saver = ex.acquire_run_slot()
    ticket = run_record.claim_start(sid, turn=1, owner="t6-old")
    ex.start_turn(sid, _user(), "hi", "req", ticket=ticket, saver=saver, turn=1)
    handle = _wait_until(lambda: ex.get_run(sid), 5, "run 应已登记")

    # 异副本接管：强制过期 + claim（若心跳恰好先续期则重试）
    t2, t_takeover = None, None
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        _expire_lease(sid)
        try:
            t2 = run_record.claim_start(sid, turn=2, owner="t6-new")
            t_takeover = time.monotonic()
            break
        except run_record.ClaimRejected:
            time.sleep(0.3)
    assert t2 is not None and t2.epoch == 2

    # 自弃：句柄清除，暴露窗口 ≤ 单 item(1.5s) + 续期间隔(1s) + 余量
    _wait_until(lambda: ex.get_run(sid) is None, 8, "旧副本应自弃并清除句柄")
    window = time.monotonic() - t_takeover
    assert window <= 1.5 + 1.0 + 1.5, f"暴露窗口 {window:.2f}s 超口径"
    assert handle.abort_reason == "taken_over"

    # 接管者行未被旧代次污染（旧副本写不进：fencing）
    row = run_record.read_run_row(sid)
    assert row.owner == "t6-new" and row.epoch == 2 and str(row.run_id) == t2.run_id
    assert row.status == "running"  # 旧副本无权写终态

    # 中止后零写入：事件恒 0、模拟 checkpoint 停止增长（观察窗 ≥ 续期间隔 + 单 item）
    assert _event_types(sid) == []
    consumed = graph.consumed
    time.sleep(2.6)
    assert graph.consumed == consumed, "自弃后不得再消费流（即不再写 checkpoint）"
    assert _event_types(sid) == []


# ---------- T9：回滚演练（flag off → 全局锁 + 池 + CAS 共存） ----------

def test_t9_flag_off_serializes_and_keeps_pool(monkeypatch, mk_session, make_exec):
    ex = make_exec(flag_on=False, pool_size=2)
    assert ex._use_global_lock is True  # flag off：全局兜底锁恢复
    assert ex._saver_pool.qsize() == 2  # saver 保持池化不变（§2.3 回滚不触碰连接管理）

    sid_a, sid_b = mk_session(), mk_session()
    sink: list = []
    trace: list = []
    graphs: dict = {}
    _install_graphs(monkeypatch, graphs)
    for sid in (sid_a, sid_b):
        script = [("sleep", 0.3), ("updates", {"n": 1}), ("sleep", 0.3), ("updates", {"n": 2})]
        graphs[sid] = FakeGraph(sid, script, sink, trace)
        saver = ex.acquire_run_slot()
        ticket = run_record.claim_start(sid, turn=1, owner=f"t9-{sid[:4]}")
        ex.start_turn(sid, _user(), "hi", "req", ticket=ticket, saver=saver, turn=1)

    _wait_until(lambda: all(ex.get_run(s) is None for s in (sid_a, sid_b)), 15, "两轮应完成")
    # 全局锁：执行区间不重叠（串行证据）
    ia = _exec_interval(trace, sid_a)
    ib = _exec_interval(trace, sid_b)
    first, second = (ia, ib) if ia[0] <= ib[0] else (ib, ia)
    assert second[0] >= first[1], "flag off 下执行区间不得重叠"
    assert ex._saver_pool.qsize() == 2  # 借还闭环
    for sid in (sid_a, sid_b):
        assert run_record.read_run_row(sid).status == "done"
        assert _event_types(sid) == ["turn/end"]


def test_t9b_reconcile_unblocks_reentry_flag_off(monkeypatch, mk_session, make_exec):
    ex = make_exec(flag_on=False, pool_size=1)
    sid = mk_session()
    dead_pid = os.getpid() + 4321  # 同主机已死进程（内存空）
    run_record.claim_start(sid, turn=1, owner=f"{socket.gethostname()}:{dead_pid}")
    assert run_record.reconcile_stale_local_runs() >= 1
    assert run_record.read_run_row(sid).status == "error"

    # 对账置 error 后旧路径立即可重入（不阻塞）
    sink: list = []
    trace: list = []
    graphs: dict = {sid: FakeGraph(sid, [("updates", {"n": 1})], sink, trace)}
    _install_graphs(monkeypatch, graphs)
    saver = ex.acquire_run_slot()
    t2 = run_record.claim_start(sid, turn=2)
    ex.start_turn(sid, _user(), "hi", "req", ticket=t2, saver=saver, turn=2)
    _wait_until(lambda: ex.get_run(sid) is None, 10, "重入轮应完成")
    assert run_record.read_run_row(sid).status == "done"
    assert t2.epoch == 2
