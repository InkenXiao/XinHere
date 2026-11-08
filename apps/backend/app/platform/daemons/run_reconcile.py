"""P9 §3.2 会话 run 落痕对账守护：只检测不自动修复。

三类异常签名（docs/p9-design.md §3.2，任一命中 = 疑似发生过并发 resume/双开）：

- ``dup_turn_start``：同一 turn 的 turn/start 事件出现两个不同 run_id ——
  两副本同时进入同一轮（互斥 CAS 破洞的直接证据）；
- ``epoch_regression``：turn/start 落痕 epoch 随 seq 倒退 —— 自弃协议失效，
  旧代次副本仍在写；
- ``owner_flip``：RunRecord 活跃行 owner 在**上轮快照租约尚未到期**时被替换 ——
  租约抖动/双活。判定依据：上轮快照 status=running 且 expires_at > 本轮扫描
  时刻的 owner 变更（合法接管必经租约过期，此条件不满足即证据确凿）；
  waiting_interrupt 的跨副本 resume 是合法翻转，不报。

留痕与出口：
- 异常写 ``platform_operation_logs``（entity=platform_session_runs、
  operation=anomaly），幂等键去重——同签名同细节只报一次，重启后不重报；
- 分桶计数经 daemon_states detail 纳入 /healthz.daemons（现有守护健康视图）；
- 人工出口三选一（ack 确认误报 / force_error 强制置错 / reclaim 换代次重置）
  由 platform/api/run_ops.py 提供，动作同样落操作日志且带幂等键。

约束：低频守护（默认 10 分钟一轮，租约单实例），SQL 全部限定回看窗口
（run_reconcile_event_window_seconds，默认 24h）防全表扫；owner_flip 依赖
进程内上轮快照，守护重启后首轮只建基线不判定。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from ...core.config import settings
from ...persistence.models import PlatformOperationLog
from ...persistence.session import SessionLocal
from . import daemon_states

logger = logging.getLogger(__name__)

SIGNATURES = ("dup_turn_start", "epoch_regression", "owner_flip")
ACTIONS = ("ack", "force_error", "reclaim")

_thread: threading.Thread | None = None
_lock = threading.Lock()
# 分桶计数（进程内累计，/healthz 数据源）与 owner 快照（上轮活跃行基线）
_buckets: dict[str, int] = {s: 0 for s in SIGNATURES}
_owner_snapshot: dict[str, dict] = {}
_last_scan: str = ""


# ---------- 单轮扫描 ----------

def reconcile_once(db: Session, owner: str = "run_reconciler") -> dict:
    """单轮对账：三类签名扫描 + 幂等留痕，返回本轮新增异常分桶计数。"""
    global _last_scan
    now = datetime.now(timezone.utc)
    found = {s: 0 for s in SIGNATURES}
    for row in db.execute(text(
        # 签名 1：同 turn 双 run_id（两副本同轮双开）
        """
        SELECT session_id, (data->>'turn')::int AS turn,
               count(DISTINCT data->>'run_id') AS run_cnt,
               min((data->>'epoch')::bigint) AS epoch_min,
               max((data->>'epoch')::bigint) AS epoch_max
        FROM platform_session_events
        WHERE type = 'turn/start' AND data ? 'run_id' AND data ? 'epoch'
          AND time >= now() - make_interval(secs => :window)
        GROUP BY session_id, (data->>'turn')::int
        HAVING count(DISTINCT data->>'run_id') > 1
        """
    ), {"window": settings.run_reconcile_event_window_seconds}):
        if _write_anomaly(db, "dup_turn_start", row.session_id, {
            "turn": row.turn, "run_cnt": int(row.run_cnt),
            "epoch_min": int(row.epoch_min or 0), "epoch_max": int(row.epoch_max or 0),
        }):
            found["dup_turn_start"] += 1

    for row in db.execute(text(
        # 签名 2：epoch 随 seq 倒退（旧代次晚于新代次写入 = 自弃失效）
        """
        SELECT a.session_id, b.seq AS earlier_seq, (b.data->>'epoch')::bigint AS earlier_epoch,
               a.seq AS later_seq, (a.data->>'epoch')::bigint AS later_epoch
        FROM platform_session_events a
        JOIN platform_session_events b
          ON a.session_id = b.session_id AND a.seq > b.seq
         AND (a.data->>'epoch')::bigint < (b.data->>'epoch')::bigint
        WHERE a.type = 'turn/start' AND b.type = 'turn/start'
          AND a.data ? 'epoch' AND b.data ? 'epoch'
          AND a.time >= now() - make_interval(secs => :window)
        """
    ), {"window": settings.run_reconcile_event_window_seconds}):
        if _write_anomaly(db, "epoch_regression", row.session_id, {
            "earlier_seq": int(row.earlier_seq), "earlier_epoch": int(row.earlier_epoch),
            "later_seq": int(row.later_seq), "later_epoch": int(row.later_epoch),
        }):
            found["epoch_regression"] += 1

    # 签名 3：owner 在租约未到期时被替换（对照上轮快照；首轮只建基线）
    active = {
        str(r.session_id): {"status": r.status, "owner": r.owner,
                            "run_id": str(r.run_id), "epoch": int(r.epoch),
                            "expires_at": r.expires_at}
        for r in db.execute(text(
            "SELECT session_id, status, owner, run_id, epoch, expires_at "
            "FROM platform_session_runs WHERE status IN ('running', 'waiting_interrupt')"
        ))
    }
    for sid, cur in active.items():
        prev = _owner_snapshot.get(sid)
        if (prev and prev["status"] == "running" and cur["status"] == "running"
                and prev["owner"] != cur["owner"] and prev["expires_at"] > now):
            if _write_anomaly(db, "owner_flip", sid, {
                "prev_owner": prev["owner"], "prev_run_id": prev["run_id"],
                "prev_expires_at": prev["expires_at"].isoformat(),
                "cur_owner": cur["owner"], "cur_run_id": cur["run_id"],
                "cur_epoch": cur["epoch"],
            }):
                found["owner_flip"] += 1
    _owner_snapshot.clear()
    _owner_snapshot.update(active)

    db.commit()
    _last_scan = now.isoformat(timespec="seconds")
    _publish_health()
    total = sum(found.values())
    if total:
        logger.warning("[run_reconcile] 对账发现 %d 条异常签名：%s", total, found)
    return found


def _write_anomaly(db: Session, signature: str, session_id, payload: dict) -> bool:
    """幂等落一条异常：同签名同细节（idem_key）已存在则跳过。返回是否新写入。"""
    sid = str(session_id)
    idem = f"run-anomaly:{signature}:{sid}:{_payload_key(signature, payload)}"
    dup = db.scalar(text(
        "SELECT count(*) FROM platform_operation_logs "
        "WHERE entity = 'platform_session_runs' AND operation = 'anomaly' "
        "  AND detail->>'idem_key' = :k AND occurred_at > now() - interval '30 days'"
    ), {"k": idem})
    if dup:
        return False
    db.add(PlatformOperationLog(
        user_id="system", session_id=uuid.UUID(sid), channel="system",
        actor="run_reconciler", entity="platform_session_runs",
        operation="anomaly", record_key=sid,
        detail={"kind": "run_anomaly", "signature": signature, "idem_key": idem,
                "payload": payload, "status": "open"},
        entry_point="daemon",
    ))
    with _lock:
        _buckets[signature] += 1
    return True


def _payload_key(signature: str, payload: dict) -> str:
    """异常幂等键的细节段：取签名判别性字段（同因重复触发不重复报）。"""
    if signature == "dup_turn_start":
        return f"turn{payload['turn']}"
    if signature == "epoch_regression":
        return f"{payload['earlier_seq']}>{payload['later_seq']}"
    return f"{payload['prev_run_id']}->{payload['cur_run_id']}"


def _publish_health() -> None:
    """分桶计数纳入 /healthz.daemons（现有守护健康视图，P9 §3.2）。"""
    daemon_states.set("run_reconciler", "running", json.dumps(
        {"buckets": dict(_buckets), "last_scan": _last_scan}, ensure_ascii=False))


def buckets_snapshot() -> dict:
    """进程内累计分桶计数（诊断/测试用）。"""
    with _lock:
        return dict(_buckets)


# ---------- 查询与人工出口（run_ops 端点调用） ----------

def list_anomalies(db: Session, session_id: str | None = None, limit: int = 50) -> list[dict]:
    """异常记录查询（含处置状态）：anomaly 行 + 最新动作状态组装。"""
    q = text(
        "SELECT id, session_id, detail, occurred_at FROM platform_operation_logs "
        "WHERE entity = 'platform_session_runs' AND operation = 'anomaly' "
        "  AND (CAST(:sid AS uuid) IS NULL OR session_id = CAST(:sid AS uuid)) "
        "ORDER BY id DESC LIMIT :lim"
    )
    rows = db.execute(q, {"sid": session_id, "lim": limit}).all()
    if not rows:
        return []
    ids = [r.id for r in rows]
    acts = db.execute(text(
        "SELECT detail->>'anomaly_id' AS aid, detail->>'action' AS action, "
        "       detail->>'idem_key' AS idem, user_id, occurred_at "
        "FROM platform_operation_logs "
        "WHERE entity = 'platform_session_runs' AND operation = 'update' "
        "  AND detail->>'kind' = 'run_anomaly_action' "
        "  AND detail->>'anomaly_id' = ANY(CAST(:ids AS text[])) "
        "ORDER BY id"
    ), {"ids": [str(i) for i in ids]}).all()
    by_anomaly: dict[str, list] = {}
    for a in acts:
        by_anomaly.setdefault(a.aid, []).append(a)
    items = []
    for r in rows:
        d = r.detail or {}
        done = by_anomaly.get(str(r.id), [])
        last = done[-1]._mapping if done else None
        items.append({
            "log_id": r.id,
            "session_id": str(r.session_id),
            "signature": d.get("signature", ""),
            "payload": d.get("payload", {}),
            "occurred_at": r.occurred_at.isoformat(),
            "handled": bool(done),
            "last_action": ({"action": last["action"], "by": last["user_id"],
                             "at": last["occurred_at"].isoformat()} if last else None),
        })
    return items


def apply_action(db: Session, session_id: str, action: str, anomaly_id: int,
                 *, actor: str, entry_point: str = "manual") -> dict:
    """人工处置三选一（P9 §3.2，幂等键防双重处置）：

    - ack：确认误报关闭（仅留痕，不动 RunRecord）；
    - force_error：对当前活跃行 fenced 置 error（行已变则 no-op）；
    - reclaim：无条件换代次（新 run_id、epoch+1、置 error）——旧代次一切
      写入立即失效，会话可立即开新轮（与 force_error 的区别即在此）。
    """
    if action not in ACTIONS:
        raise ValueError(f"不支持的处理动作: {action}")
    row = db.execute(text(
        "SELECT id, session_id, detail FROM platform_operation_logs "
        "WHERE id = :id AND entity = 'platform_session_runs' AND operation = 'anomaly'"
    ), {"id": anomaly_id}).first()
    if row is None or str(row.session_id) != session_id:
        raise LookupError("异常记录不存在")
    idem = f"run-anomaly-action:{action}:{anomaly_id}"
    dup = db.scalar(text(
        "SELECT count(*) FROM platform_operation_logs "
        "WHERE detail->>'idem_key' = :k AND occurred_at > now() - interval '30 days'"
    ), {"k": idem})
    if dup:
        return {"action": action, "duplicate": True}

    result: dict = {"action": action, "duplicate": False}
    sid = uuid.UUID(session_id)
    if action == "force_error":
        result["changed"] = db.execute(text(
            "UPDATE platform_session_runs SET status = 'error', updated_at = now(), "
            "    detail = detail || '{\"reason\":\"manual_force_error\"}'::jsonb "
            "WHERE session_id = :sid AND status IN ('running', 'waiting_interrupt')"
        ), {"sid": sid}).rowcount
    elif action == "reclaim":
        result["changed"] = db.execute(text(
            "UPDATE platform_session_runs "
            "SET run_id = :new_run, epoch = epoch + 1, status = 'error', "
            "    expires_at = now(), heartbeat_at = now(), updated_at = now(), "
            "    detail = detail || '{\"reason\":\"manual_reclaim\"}'::jsonb "
            "WHERE session_id = :sid AND status IN ('running', 'waiting_interrupt')"
        ), {"sid": sid, "new_run": uuid.uuid4()}).rowcount

    db.add(PlatformOperationLog(
        user_id=actor, session_id=sid, channel="system", actor=actor,
        entity="platform_session_runs", operation="update", record_key=session_id,
        detail={"kind": "run_anomaly_action", "action": action, "anomaly_id": anomaly_id,
                "idem_key": idem, **result},
        entry_point=entry_point,
    ))
    db.commit()
    logger.info("[run_reconcile] 人工处置 sid=%s anomaly=%d action=%s by=%s changed=%s",
                session_id, anomaly_id, action, actor, result.get("changed"))
    return result


# ---------- 守护装配 ----------

def start_reconciler() -> None:
    """启动对账线程（由 main._guarded_daemon 装配：租约互斥由外层负责）。"""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    interval = float(settings.run_reconcile_interval_seconds)

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                with SessionLocal() as db:
                    reconcile_once(db)
            except Exception:  # noqa: BLE001 - 单轮异常不拖垮主进程
                logger.exception("[run_reconcile] 扫描轮异常")
                daemon_states.set("run_reconciler", "retrying", "扫描轮异常")

    _thread = threading.Thread(target=_loop, name="daemon-run-reconciler", daemon=True)
    _thread.start()
    logger.info("[run_reconcile] 落痕对账守护已启动 interval=%.0fs window=%.0fs",
                interval, float(settings.run_reconcile_event_window_seconds))
