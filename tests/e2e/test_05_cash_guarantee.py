"""矩阵 5：现金保障倍数（模型 + interrupt 生命周期）。

- 伪造 interrupt_id → 409 INTERRUPT_MISMATCH；
- 正确 submit → resume 续跑模型总结 → 落库 ratio/lamp/status；
- cancel → component/submit{cancelled} 收尾，baseline.pending.interrupts 空（不裸挂）。
"""
from __future__ import annotations

import httpx
import pytest

from .conftest import (
    API, BASE_URL, COMPANY_INV01, MODEL_TIMEOUT, PERIOD, chat_until_component,
    collect_sse, db_one, submit_component, wait_for_event, wait_until,
)

VALUES = {"avail_cash": 1500.0, "pooled_fund": 800.0,
          "avail_credit": 2000.0, "monthly_outflow": 900.0}
EXPECT_RATIO = (1500.0 + 800.0 + 2000.0) / 900.0  # ≈4.78 → y


def _trigger(hq, tag: str):
    return chat_until_component(
        hq, f"现金保障{tag}",
        f"立即调用 start_cash_guarantee_fill 工具，company={COMPANY_INV01}，"
        f"period={PERIOD}，发起现金保障倍数填报。",
        kind="cash-guarantee-form",
    )


def test_cash_guarantee_submit_flow(hq, db):
    sid, comp, _ = _trigger(hq, "提交")
    assert comp.get("interrupt_id")
    req_seq = comp["seq"]

    # ① 伪造 interrupt_id → 409 INTERRUPT_MISMATCH
    hq.err(
        "POST", f"/sessions/{sid}/components/{comp['component_id']}/submit", 409,
        "INTERRUPT_MISMATCH",
        json={"action": "submit", "values": VALUES, "interrupt_id": "fake-id"},
    )

    # ② 正确 submit → resume 续跑（component/request 之后出现新的 assistant/message）
    submit_component(hq, sid, comp["component_id"], "submit",
                     comp["interrupt_id"], values=VALUES)
    msg, _ = wait_for_event(
        hq, sid,
        lambda it: it["type"] == "assistant/message" and it["seq"] > req_seq,
        after_seq=req_seq,
    )
    assert msg["data"].get("content"), "resume 后模型总结为空"
    wait_for_event(hq, sid, lambda it: it["type"] == "turn/end", after_seq=req_seq)

    # 落库：按组件 props.form_id 精确核对（避免其它矩阵遗留草稿干扰"最新行"查询）
    form_id = comp["props"]["form_id"]
    row = wait_until(lambda: db_one(
        db,
        "SELECT ratio, lamp, status FROM cash_guarantee_reports WHERE form_id=%s",
        (form_id,),
    ))
    assert row, "cash_guarantee_reports 未落库"
    ratio, lamp, status = row
    assert abs(float(ratio) - EXPECT_RATIO) < 0.01, f"ratio 偏差: {ratio}"
    assert lamp == "y" and status == "submitted"


def test_cash_guarantee_cancel_flow(hq, db, hq_auth):
    sid, comp, _ = _trigger(hq, "取消")

    # cancel → component/submit{action:'cancelled'} 收尾
    submit_component(hq, sid, comp["component_id"], "cancel", comp["interrupt_id"])
    sub, _ = wait_for_event(
        hq, sid,
        lambda it: it["type"] == "component/submit"
        and it["data"].get("component_id") == comp["component_id"],
    )
    assert sub["data"].get("action") == "cancelled", f"cancel 收尾动作偏差: {sub['data']}"

    # 不裸挂：baseline.pending.interrupts 为空
    with httpx.stream(
        "GET", f"{BASE_URL}{API}/sessions/{sid}/events",
        headers={"Authorization": f"Bearer {hq_auth['token']}",
                 "Accept": "text/event-stream"},
        timeout=httpx.Timeout(30.0, read=MODEL_TIMEOUT),
    ) as resp:
        assert resp.status_code == 200
        frames = collect_sse(resp, max_frames=1)
    assert frames and frames[0]["event"] == "baseline", f"首帧应为 baseline: {frames[:1]}"
    pending = frames[0]["data"].get("pending", {})
    assert pending.get("interrupts") == [], f"cancel 后仍有挂起 interrupt: {pending}"
