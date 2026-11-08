"""P2 复核证据：event_ignorable 双维度解耦——注册维度与前端渲染维度分离。

- 事件类型已注册 → 可写入事件日志（validate_payload fail-closed 校验负载结构）；
- ignorable 只影响前端装配器跳过（无渲染器时的豁免），**不影响日志写入**；
- 未知类型 fail-closed：不落库、不发布。
"""
from __future__ import annotations

import pytest

from app.platform.events.registry import REGISTRY
from app.platform.events.store import frame_of, store


def test_unknown_type_fail_closed_no_persist(db, mk_session):
    """写入维度 fail-closed：未知事件类型拒绝写入事件日志。"""
    sid = mk_session()
    with pytest.raises(ValueError, match="未知事件类型"):
        store.append(sid, "ghost/unknown", {"x": 1})
    rows, has_more = store.list_events(sid)
    assert rows == [] and not has_more


def test_ignorable_event_persisted_and_frame_marked(db, mk_session):
    """日志维度与渲染维度解耦：ignorable 事件照常落库（轨迹可追溯），
    帧带 ignorable=True 供前端装配器跳过——写入不受 ignorable 影响。"""
    sid = mk_session()
    seq, _t = store.append(sid, "subagent/start",
                           {"name": "general", "provider": "general", "depth": 1})
    assert seq == 0
    rows, _ = store.list_events(sid)
    assert len(rows) == 1 and rows[0].type == "subagent/start"
    assert rows[0].ignorable is True  # 日志维度：照常写入
    frame = frame_of(rows[0])
    assert frame["data"]["ignorable"] is True  # 渲染维度：前端据此跳过
    # 注册表中两个维度是独立字段：已注册（可写）≠ 前端必须有渲染器
    assert REGISTRY["subagent/start"][1] is True


def test_renderable_event_persisted_frame_not_ignorable(db, mk_session):
    """可渲染事件：日志写入且帧 ignorable=False（前端必须有渲染器，缺失即 fail-closed）。"""
    sid = mk_session()
    store.append(sid, "user/message", {"content": "hi", "source": "human", "version": 1})
    rows, _ = store.list_events(sid)
    assert rows[0].ignorable is False
    assert frame_of(rows[0])["data"]["ignorable"] is False
    assert REGISTRY["user/message"][1] is False
