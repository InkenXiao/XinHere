from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.persistence.models import SysUser
from app.platform.api.sessions import _do_component_submit
from app.platform.events.store import store


def test_interrupt_lifecycle(db, mk_session):
    sid = mk_session()
    store.append(
        sid,
        "component/request",
        {
            "component_id": "c1",
            "kind": "risk-dispatch-confirm",
            "kind_version": 1,
            "props": {"period": "2026-08", "companies": ["信投股份"]},
            "interrupt_id": "real-id",
            "version": 1,
        },
        publish=False,
    )
    # cancel 路径仅取 user_id，内存对象即可（不入库）
    user = SysUser(
        user_id="pytest", username="pytest", password_hash="x",
        display_name="pytest", role="hq_finance",
    )

    # 错误 interrupt_id → INTERRUPT_MISMATCH
    with pytest.raises(AppError) as ei:
        _do_component_submit(db, sid, "c1", "cancel", None, "wrong-id", user)
    assert ei.value.code == "INTERRUPT_MISMATCH"

    # 正向 cancel：落 component/submit{action:"cancelled"}
    _seq, summary = _do_component_submit(db, sid, "c1", "cancel", None, "real-id", user)
    assert summary == "用户取消了本次操作"
    rows, _ = store.list_events(sid, limit=100)
    submits = [r for r in rows if r.type == "component/submit"]
    assert len(submits) == 1
    assert submits[0].data["action"] == "cancelled"

    # 重复提交同一 component_id → VALIDATION_ERROR（不可重复操作）
    with pytest.raises(AppError) as ei2:
        _do_component_submit(db, sid, "c1", "cancel", None, "real-id", user)
    assert ei2.value.code == "VALIDATION_ERROR"
