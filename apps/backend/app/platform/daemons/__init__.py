"""后台守护基座：DB 租约互斥 + supervisor 自愈（§4.9）

用法（守护统一写法）::

    from app.platform.daemons import DaemonSupervisor, lease

    supervisor = DaemonSupervisor("meeting_watchdog", run_once_fn, interval=30)
    supervisor.start()          # main.py lifespan 中启动（守护随开关配置启停）

    async/sync def run_once_fn(db, owner) -> None: ...   # 单轮逻辑，异常由 supervisor 捕获

- 租约互斥：启动抢 platform_daemon_locks 租约（lock_key+expires_at）；持锁者续期，
  过期其他副本接管 → 多副本部署同一守护全局仅一份活跃。
- 自愈：run_once 异常 → 指数退避重启（1s→2s→…上限 5min）；连续 5 次熔断为
  circuit-broken 并告警日志；任何情况不冒泡拖垮 FastAPI 主进程。
- 可观测：/healthz 的 daemons 段（running/retrying/circuit-broken/locked_by_other）。
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import text

from ...core.config import settings
from ...persistence.models import PlatformDaemonLock
from ...persistence.session import SessionLocal

logger = logging.getLogger(__name__)

_OWNER = f"{socket.gethostname()}:{__import__('os').getpid()}"
_MAX_BACKOFF = 300.0  # 退避上限 5 分钟
_CIRCUIT_BREAK_AFTER = 5  # 连续失败 N 次熔断


class DaemonState:
    """守护运行态登记（/healthz.daemons 数据源）。"""

    def __init__(self) -> None:
        self._states: dict[str, dict] = {}

    def set(self, key: str, status: str, detail: str = "") -> None:
        self._states[key] = {"status": status, "detail": detail, "ts": time.time()}

    def snapshot(self) -> dict:
        return {k: {"status": v["status"], "detail": v["detail"]} for k, v in self._states.items()}


daemon_states = DaemonState()


def acquire_or_renew(lock_key: str, ttl_seconds: float = 90.0) -> bool:
    """抢占/续期租约：True=本副本持有（可执行本轮）。"""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.get(PlatformDaemonLock, lock_key)
        if row is None:
            db.add(PlatformDaemonLock(
                lock_key=lock_key, owner=_OWNER,
                heartbeat_at=now, expires_at=now + timedelta(seconds=ttl_seconds),
                acquired_at=now,
            ))
            try:
                db.commit()
                logger.info("[daemon:%s] 租约抢占成功 owner=%s", lock_key, _OWNER)
                return True
            except Exception:
                db.rollback()
                return False  # 并发抢锁失败
        if row.owner == _OWNER or row.expires_at < now:
            # 本人续期，或前任租约过期 → 接管
            took_over = row.owner != _OWNER
            row.owner = _OWNER
            row.heartbeat_at = now
            row.expires_at = now + timedelta(seconds=ttl_seconds)
            if took_over:
                row.acquired_at = now
                logger.warning("[daemon:%s] 前任租约过期，接管 owner=%s", lock_key, _OWNER)
            db.commit()
            return True
        return False  # 其他副本持有且未过期


def release(lock_key: str) -> None:
    with SessionLocal() as db:
        row = db.get(PlatformDaemonLock, lock_key)
        if row is not None and row.owner == _OWNER:
            db.delete(row)
            db.commit()


class DaemonSupervisor:
    """单守护 supervisor：租约轮询 + 异常退避 + 熔断。"""

    def __init__(
        self,
        name: str,
        run_once: Callable,
        interval: float = 30.0,
        lease_ttl: float = 90.0,
    ) -> None:
        self.name = name
        self.run_once = run_once
        self.interval = interval
        self.lease_ttl = lease_ttl
        self._task: asyncio.Task | None = None
        self._fail_streak = 0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_event_loop().create_task(self._run())
            daemon_states.set(self.name, "starting")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            release(self.name)
            daemon_states.set(self.name, "stopped")

    async def _run(self) -> None:
        backoff = min(self.interval, 1.0)
        try:
            while True:
                try:
                    if acquire_or_renew(self.name, self.lease_ttl):
                        await self._invoke()
                        self._fail_streak = 0
                        backoff = min(self.interval, 1.0)
                        daemon_states.set(self.name, "running")
                    else:
                        daemon_states.set(self.name, "standby", "其他副本持有租约")
                    await asyncio.sleep(self.interval)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._fail_streak += 1
                    if self._fail_streak >= _CIRCUIT_BREAK_AFTER:
                        daemon_states.set(self.name, "circuit-broken", f"连续失败 {self._fail_streak} 次")
                        logger.exception("[daemon:%s] 连续失败 %d 次，熔断告警（不再重启）",
                                         self.name, self._fail_streak)
                        return
                    daemon_states.set(self.name, "retrying", f"第 {self._fail_streak} 次失败，{backoff:.0f}s 后重试")
                    logger.exception("[daemon:%s] 单轮异常（第 %d 次），%.0fs 后重试",
                                     self.name, self._fail_streak, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF)
        except asyncio.CancelledError:
            logger.info("[daemon:%s] 退出", self.name)
            raise

    async def _invoke(self) -> None:
        import inspect
        with SessionLocal() as db:
            if inspect.iscoroutinefunction(self.run_once):
                await self.run_once(db, _OWNER)
            else:
                await asyncio.to_thread(self.run_once, db, _OWNER)
