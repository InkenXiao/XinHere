"""P9 会话 run 互斥锚点：platform_session_runs 的 claim / 续期 / fenced 落终态。

设计要点（docs/p9-design.md §1 / §2.2 / §3.1 / §5.2）：
- claim = 条件 UPSERT（无行 / 终态 / 租约过期 → 置 running），互斥与登记同一语句原子
  完成，闭合「先查后登」TOCTOU 双开窗；影响 0 行 = 会话忙，重读行分类 reason；
- start 与 resume 两类 claim 条件不同：
  - start：上一轮终态（done/error）或租约过期（崩溃判定）才可开新轮；
  - resume：waiting_interrupt（等待中的声明不被执行占用，任意副本可解挂——翻转
    成功者获得执行权，败者拒绝）；或 running 且租约过期（崩溃孤儿中断恢复，T7）；
- 续期与落终态一律以 run_id 为 fencing 条件：行已被接管（run_id 变更）时影响
  0 行，旧代次无法写入；
- 本模块全部是 RunRecord 行上的毫秒级短事务，走引擎连接池，绝不复用 run 独占的
  checkpointer 连接（池压力不得延迟续期，防自弃误触发，P9 §2.3）。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text

from ...core.config import settings
from ...persistence.session import SessionLocal

logger = logging.getLogger(__name__)


class ClaimRejected(Exception):
    """claim 条件更新影响 0 行：会话忙。reason ∈ busy | taken_over（P9 §3.1）。

    - busy：租约仍有效且未被并发迁移（另一 run 活着）→ 客户端可退避重试；
    - taken_over：租约已过期 / 行被并发接管 → 客户端不重试，刷新会话状态重新拉取。
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RunTicket:
    """claim 成功产物：fencing 三元组（run_id / epoch / owner）。"""

    session_id: str
    run_id: str
    epoch: int
    owner: str


def owner_id() -> str:
    """持有者标识：host:pid（跨副本唯一性由主机名保证，进程内由 pid 保证）。"""
    return f"{socket.gethostname()}:{os.getpid()}"


# ---------- claim（条件 UPSERT，单语句原子） ----------

_CLAIM_COLS = """(
    session_id, run_id, status, owner, epoch, turn,
    user_id, request_id, model_key, skill_key, skill_source,
    expires_at, heartbeat_at, detail, created_at, updated_at
)"""

_CLAIM_VALS = """(
    :sid, :run_id, 'running', :owner, 1, :turn,
    :user_id, :request_id, :model_key, :skill_key, :skill_source,
    now() + make_interval(secs => :ttl), now(), '{}'::jsonb, now(), now()
)"""

_CLAIM_SET = """
SET run_id = EXCLUDED.run_id,
    status = 'running',
    owner = EXCLUDED.owner,
    epoch = platform_session_runs.epoch + 1,
    turn = COALESCE(EXCLUDED.turn, platform_session_runs.turn),
    user_id = EXCLUDED.user_id,
    request_id = EXCLUDED.request_id,
    model_key = EXCLUDED.model_key,
    skill_key = EXCLUDED.skill_key,
    skill_source = EXCLUDED.skill_source,
    interrupt_id = NULL,
    component_id = NULL,
    expires_at = now() + make_interval(secs => :ttl),
    heartbeat_at = now(),
    detail = '{}'::jsonb,
    updated_at = now()
"""

# start：上一轮已终态 / 租约过期（崩溃判定）才可开新轮。
# waiting_interrupt 且租约有效 → 不可开新轮（有未处理卡片，行为对齐原 run_busy）
_CLAIM_WHERE_START = """(
    platform_session_runs.status IN ('done', 'error')
    OR platform_session_runs.expires_at < now()
)"""

# resume：翻转成功者获得执行权——
# a) waiting_interrupt：等待声明不被执行占用，任意副本可解挂（跨副本 resume，T3）；
# b) running 且租约过期：崩溃孤儿中断恢复（恢复由 RunRecord 驱动，T7）
_CLAIM_WHERE_RESUME = """(
    platform_session_runs.status = 'waiting_interrupt'
    OR (platform_session_runs.status = 'running' AND platform_session_runs.expires_at < now())
)"""


def claim_start(session_id: str, *, turn: int, owner: str | None = None,
                user_id: str = "", request_id: str = "",
                model_key: str = "", skill_key: str = "", skill_source: str = "") -> RunTicket:
    """新轮 claim：条件满足才置 running（新 run_id、epoch+1、新租约），否则 ClaimRejected。"""
    return _claim(
        _CLAIM_WHERE_START, session_id=session_id, turn=turn, owner=owner,
        user_id=user_id, request_id=request_id,
        model_key=model_key, skill_key=skill_key, skill_source=skill_source,
    )


def claim_resume(session_id: str, *, owner: str | None = None) -> RunTicket:
    """resume claim：waiting_interrupt → running 翻转（或崩溃 running 过期接管）。"""
    return _claim(
        _CLAIM_WHERE_RESUME, session_id=session_id, turn=None, owner=owner,
    )


def _claim(where: str, *, session_id: str, turn: int | None, owner: str | None,
           user_id: str = "", request_id: str = "",
           model_key: str = "", skill_key: str = "", skill_source: str = "") -> RunTicket:
    owner = owner or owner_id()
    params = {
        "sid": uuid.UUID(session_id), "run_id": uuid.uuid4(), "owner": owner, "turn": turn,
        "user_id": user_id or "", "request_id": request_id or "", "model_key": model_key or "",
        "skill_key": skill_key or "", "skill_source": skill_source or "",
        "ttl": settings.run_lease_ttl_seconds,
    }
    sql = text(
        f"INSERT INTO platform_session_runs {_CLAIM_COLS} VALUES {_CLAIM_VALS} "
        f"ON CONFLICT (session_id) DO UPDATE {_CLAIM_SET} WHERE {where} "
        f"RETURNING run_id, epoch"
    )
    with SessionLocal() as db:
        row = db.execute(sql, params).first()
        db.commit()
    if row is None:
        raise ClaimRejected(_classify(session_id))
    logger.info("run claim 成功 sid=%s run=%s epoch=%s owner=%s", session_id, row.run_id, row.epoch, owner)
    return RunTicket(session_id=session_id, run_id=str(row.run_id), epoch=int(row.epoch), owner=owner)


def _classify(session_id: str) -> str:
    """claim 失败后重读行分类：租约已过期 = taken_over，否则 busy（P9 §3.1）。"""
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT expires_at FROM platform_session_runs WHERE session_id = :sid"),
            {"sid": uuid.UUID(session_id)},
        ).first()
        db.commit()
    if row is not None and row.expires_at < datetime.now(timezone.utc):
        return "taken_over"
    return "busy"


# ---------- 续期与 fenced 状态翻转 ----------

def renew(session_id: str, run_id: str) -> bool:
    """租约续期（fenced by run_id）。False = 所有权已丢失（被接管/已终态）→ 触发自弃。"""
    sql = text(
        "UPDATE platform_session_runs "
        "SET expires_at = now() + make_interval(secs => :ttl), heartbeat_at = now(), updated_at = now() "
        "WHERE session_id = :sid AND run_id = :rid AND status IN ('running', 'waiting_interrupt')"
    )
    with SessionLocal() as db:
        n = db.execute(sql, {"sid": uuid.UUID(str(session_id)), "rid": uuid.UUID(str(run_id)),
                             "ttl": settings.run_lease_ttl_seconds}).rowcount
        db.commit()
    return n == 1


def mark(session_id: str, run_id: str, status: str, *,
         interrupt_id: str | None = None, component_id: str | None = None,
         detail: dict | None = None) -> bool:
    """fenced 状态翻转（waiting_interrupt / done / error）。False = 所有权已丢失，禁止落库。"""
    sql = text(
        "UPDATE platform_session_runs "
        "SET status = :status, updated_at = now(), "
        "    interrupt_id = COALESCE(CAST(:interrupt_id AS varchar), platform_session_runs.interrupt_id), "
        "    component_id = COALESCE(CAST(:component_id AS varchar), platform_session_runs.component_id), "
        "    detail = COALESCE(CAST(:detail AS JSONB), platform_session_runs.detail) "
        "WHERE session_id = :sid AND run_id = :rid AND status IN ('running', 'waiting_interrupt')"
    )
    with SessionLocal() as db:
        n = db.execute(sql, {"sid": uuid.UUID(str(session_id)), "rid": uuid.UUID(str(run_id)), "status": status,
                             "interrupt_id": interrupt_id, "component_id": component_id,
                             "detail": json.dumps(detail, ensure_ascii=False) if detail else None}).rowcount
        db.commit()
    return n == 1


# ---------- 对账 ----------

def reconcile_stale_local_runs() -> int:
    """启动对账：本机既往进程遗留的非终态行统一置 error（P9 §5.2 / T9）。

    只清理本机 owner（host 前缀匹配且非本进程）：他机活跃副本的行不动，
    其接管仍靠租约过期 + claim CAS。错误行不阻塞旧恢复链（recover_crashed 兜底）。
    """
    host = socket.gethostname()
    me = owner_id()
    sql = text(
        "UPDATE platform_session_runs "
        "SET status = 'error', updated_at = now(), "
        "    detail = platform_session_runs.detail || jsonb_build_object('reason', 'startup_reconcile') "
        "WHERE status IN ('running', 'waiting_interrupt') "
        "  AND owner LIKE :host || ':%' AND owner <> :me"
    )
    with SessionLocal() as db:
        n = db.execute(sql, {"host": host, "me": me}).rowcount
        db.commit()
    if n:
        logger.warning("启动对账：本机遗留非终态 run 置 error 共 %d 行（host=%s）", n, host)
    else:
        logger.info("启动对账：无本机遗留非终态 run")
    return n


def read_run_row(session_id: str):
    """读取 RunRecord 行（对账/测试/诊断用）。"""
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT session_id, run_id, status, owner, epoch, turn, expires_at "
                 "FROM platform_session_runs WHERE session_id = :sid"),
            {"sid": uuid.UUID(session_id)},
        ).first()
        db.commit()
    return row
