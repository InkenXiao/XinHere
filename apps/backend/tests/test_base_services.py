"""WP2 基础服务层单测：storage 本地回退 / 守护租约互斥与自愈 / todo_bus 事件总线"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.platform.daemons import DaemonSupervisor, acquire_or_renew, daemon_states, release
from app.platform.events import todo_bus
from app.services import storage


def test_storage_local_fallback_roundtrip(tmp_path):
    import app.core.config as cc
    old = (cc.settings.minio_endpoint, cc.settings.artifact_dir)
    cc.settings.minio_endpoint = ""
    cc.settings.artifact_dir = str(tmp_path)
    try:
        storage.put("reports/test/a.txt", b"hello-xinhere")
        assert storage.read("reports/test/a.txt") == b"hello-xinhere"
        assert storage.exists("reports/test/a.txt") is True
    finally:
        cc.settings.minio_endpoint, cc.settings.artifact_dir = old


def test_storage_missing_key_raises():
    import app.core.config as cc
    old = cc.settings.minio_endpoint
    cc.settings.minio_endpoint = ""
    try:
        with pytest.raises(FileNotFoundError):
            storage.read("reports/__no_such_file__.txt")
    finally:
        cc.settings.minio_endpoint = old


def test_daemon_lease_mutual_exclusion(db):
    from app.persistence.models import PlatformDaemonLock
    key = "test-daemon-x"
    release(key)
    assert acquire_or_renew(key, ttl_seconds=60) is True
    assert acquire_or_renew(key, ttl_seconds=60) is True  # 同 owner 续期
    row = db.get(PlatformDaemonLock, key)
    if row is None:
        db.add(PlatformDaemonLock(lock_key=key, owner="other:1",
                                  expires_at=datetime.now(timezone.utc) + timedelta(seconds=60)))
        db.commit()
    else:
        row.owner = "other:1"
        row.expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)
        db.commit()
    assert acquire_or_renew(key, ttl_seconds=60) is False  # 他人持有 → 互斥
    row = db.get(PlatformDaemonLock, key)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert acquire_or_renew(key, ttl_seconds=60) is True  # 过期 → 接管
    release(key)


def test_daemon_supervisor_circuit_break(db):
    calls = {"n": 0}

    def boom(db_, owner):
        calls["n"] += 1
        raise RuntimeError("boom")

    sup = DaemonSupervisor("test-daemon-y", boom, interval=0.01, lease_ttl=5)

    async def run():
        task = asyncio.get_event_loop().create_task(sup._run())
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert calls["n"] >= 5
    assert daemon_states.snapshot()["test-daemon-y"]["status"] == "circuit-broken"
    release("test-daemon-y")


def test_todo_bus_subscribe_publish():
    q = todo_bus.subscribe("u-x")
    todo_bus.publish(["u-x"], "todo/changed", {"todo_id": "t1"})
    todo_bus.publish(["u-other"], "todo/changed", {})
    ev = q.get(timeout=1)
    assert ev["type"] == "todo/changed" and ev["data"]["todo_id"] == "t1"
    assert q.empty()  # 他人不投递
    todo_bus.unsubscribe("u-x", q)
