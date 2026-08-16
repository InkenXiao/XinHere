from __future__ import annotations

import logging
import threading
import time
import uuid as uuidlib
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select

from ...persistence.models import PlatformSession, PlatformSessionEvent
from ...persistence.session import SessionLocal
from ..agent.stream_bridge import bridge
from .registry import default_ignorable, validate_payload

_CHUNK_FLUSH_INTERVAL = 0.2  # 200ms
_CHUNK_FLUSH_SIZE = 64

logger = logging.getLogger(__name__)


class EventStore:
    """事件追加唯一入口：锁会话头行 → max(seq)+1 → insert。

    chunk 类事件进内存队列，200ms 或满 64 条批量 flush（单事务）；
    写 assistant/message 前必须 flush_chunks 拿到 chunk 的 seq 完成配对。
    """

    def __init__(self) -> None:
        self._queues: dict[str, list[dict]] = defaultdict(list)
        self._qlock = threading.Lock()
        self._cond = threading.Condition()
        self._flock = threading.Lock()  # flush 串行化：pop+落库+记账 对 flush/drain 互斥
        self._flushed_seqs: dict[str, list[int]] = {}  # sid → flusher 已落库、待配对的 chunk seq
        self._stopped = False
        self._flusher = threading.Thread(target=self._flush_loop, name="chunk-flusher", daemon=True)
        self._flusher.start()

    # ---------- 写路径 ----------

    def append(
        self,
        session_id: str,
        type_: str,
        data: dict,
        *,
        ignorable: bool | None = None,
        turn: int | None = None,
        publish: bool = True,
    ) -> tuple[int, datetime]:
        payload = validate_payload(type_, data)
        sid = uuidlib.UUID(session_id)
        with SessionLocal() as db:
            # 锁 platform_sessions 父行：会话内写串行化
            db.execute(
                select(PlatformSession.session_id)
                .where(PlatformSession.session_id == sid)
                .with_for_update()
            )
            # 聚合查询禁 FOR UPDATE
            next_seq = db.scalar(
                select(func.coalesce(func.max(PlatformSessionEvent.seq), -1) + 1).where(
                    PlatformSessionEvent.session_id == sid
                )
            )
            row = PlatformSessionEvent(
                session_id=sid,
                seq=next_seq,
                type=type_,
                data=payload,
                ignorable=default_ignorable(type_) if ignorable is None else ignorable,
                turn=turn,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            evt_time = row.time
        if type_ != "assistant/chunk":
            logger.info("事件落库 sid=%s seq=%d type=%s turn=%s", session_id, next_seq, type_, turn)
        if publish:
            self._publish(session_id, next_seq, type_, evt_time, payload)
        return next_seq, evt_time

    def append_chunk(self, session_id: str, data: dict, *, turn: int | None = None) -> None:
        """chunk 类批量写：进内存队列，由 flusher 统一分配 seq。"""
        payload = validate_payload("assistant/chunk", data)
        with self._cond:
            q = self._queues[session_id]
            q.append({"type": "assistant/chunk", "data": payload, "turn": turn})
            if len(q) >= _CHUNK_FLUSH_SIZE:
                self._cond.notify_all()

    def flush_chunks(self, session_id: str) -> list[tuple[int, datetime, dict]]:
        """强制 flush 指定会话 chunk 队列，返回 (seq, time, payload) 列表。"""
        return self._flush(session_id, record=False)

    def _flush_and_record(self, session_id: str) -> None:
        """flusher 线程专用：批量落库后把 seq 记入本 step 累积区（待 finalize 配对）。"""
        self._flush(session_id, record=True)

    def _flush(self, session_id: str, *, record: bool) -> list[tuple[int, datetime, dict]]:
        # _flock 保证 pop+落库+记账 相对其他 flush/drain 原子，flusher 与 finalize 不错位
        with self._flock:
            with self._cond:
                batch = self._queues.pop(session_id, [])
            if not batch:
                return []
            out = self._insert_batch(session_id, batch)
            if record:
                with self._cond:
                    self._flushed_seqs.setdefault(session_id, []).extend(
                        seq for seq, _t, _d in out
                    )
            return out

    def drained_chunk_seqs(self, session_id: str) -> list[int]:
        """本 step 全部 chunk seq：flusher 已落库累积区 + 队列残余强制 flush；清空累积区。"""
        with self._flock:
            with self._cond:
                acc = self._flushed_seqs.pop(session_id, [])
                batch = self._queues.pop(session_id, [])
            out = self._insert_batch(session_id, batch) if batch else []
        return acc + [seq for seq, _t, _d in out]

    def clear_chunk_state(self, session_id: str) -> None:
        """turn 边界清理：防已结束会话的队列键/累积区滞留内存。"""
        with self._cond:
            self._queues.pop(session_id, None)
            self._flushed_seqs.pop(session_id, None)

    def _insert_batch(self, session_id: str, batch: list[dict]) -> list[tuple[int, datetime, dict]]:
        sid = uuidlib.UUID(session_id)
        out: list[tuple[int, datetime, dict]] = []
        with SessionLocal() as db:
            db.execute(
                select(PlatformSession.session_id)
                .where(PlatformSession.session_id == sid)
                .with_for_update()
            )
            next_seq = db.scalar(
                select(func.coalesce(func.max(PlatformSessionEvent.seq), -1) + 1).where(
                    PlatformSessionEvent.session_id == sid
                )
            )
            for i, item in enumerate(batch):
                db.add(
                    PlatformSessionEvent(
                        session_id=sid,
                        seq=next_seq + i,
                        type=item["type"],
                        data=item["data"],
                        ignorable=False,
                        turn=item.get("turn"),
                    )
                )
            db.commit()
            rows = db.execute(
                select(PlatformSessionEvent)
                .where(PlatformSessionEvent.session_id == sid, PlatformSessionEvent.seq >= next_seq)
                .order_by(PlatformSessionEvent.seq)
            ).scalars().all()
            for row in rows:
                out.append((row.seq, row.time, row.data))
        for seq, t, payload in out:
            self._publish(session_id, seq, "assistant/chunk", t, payload)
        return out

    def _flush_loop(self) -> None:
        while not self._stopped:
            with self._cond:
                self._cond.wait(timeout=_CHUNK_FLUSH_INTERVAL)
                due = [sid for sid, q in self._queues.items() if q]
            for sid in due:
                try:
                    self._flush_and_record(sid)
                except Exception:
                    # flush 失败不崩线程，下轮重试
                    logger.warning("chunk flush 失败（下轮重试） sid=%s", sid, exc_info=True)
                    time.sleep(0.05)

    # ---------- 读路径 ----------

    def list_events(
        self, session_id: str, after_seq: int = -1, limit: int = 200
    ) -> tuple[list[PlatformSessionEvent], bool]:
        sid = uuidlib.UUID(session_id)
        with SessionLocal() as db:
            rows = (
                db.execute(
                    select(PlatformSessionEvent)
                    .where(PlatformSessionEvent.session_id == sid, PlatformSessionEvent.seq > after_seq)
                    .order_by(PlatformSessionEvent.seq)
                    .limit(limit + 1)
                )
                .scalars()
                .all()
            )
        has_more = len(rows) > limit
        return rows[:limit], has_more

    def max_seq(self, session_id: str) -> int:
        sid = uuidlib.UUID(session_id)
        with SessionLocal() as db:
            return db.scalar(
                select(func.coalesce(func.max(PlatformSessionEvent.seq), -1)).where(
                    PlatformSessionEvent.session_id == sid
                )
            )

    def _publish(self, session_id: str, seq: int, type_: str, t: datetime, payload: dict) -> None:
        bridge.publish(
            session_id,
            {
                "type": type_,
                "id": f"{session_id}:{seq}",
                "data": {"seq": seq, "time": t.isoformat(), **payload},
            },
        )


store = EventStore()


def frame_of(row: PlatformSessionEvent) -> dict:
    return {
        "type": row.type,
        "id": f"{row.session_id}:{row.seq}",
        "data": {"seq": row.seq, "time": row.time.isoformat(), **row.data},
    }
