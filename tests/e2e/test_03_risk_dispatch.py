"""矩阵 3：发起风险填报（模型路径 + AI 溯源）。

hq01 对话指令 → 模型调 dispatch_risk_fill → interrupt 出 risk-dispatch-confirm
→ 确认 → 续跑至 turn/end → risk/fill-start 事件 → 11 公司 report+待办落库
→ op-log channel='tool' 五要素溯源（actor 含工具名/session_id/detail.context）。
"""
from __future__ import annotations

import time

from .conftest import (
    E2E, PERIOD, chat_until_component, db_all, db_one, submit_component,
    wait_for_event, wait_until,
)

COMPANIES_11 = [
    "信投智造", "信投新能", "信投医疗", "信投数科", "信投物流", "信投环保",
    "信投半导", "信投云联", "信投金服", "信投教育", "信投文旅",
]


def test_risk_dispatch_via_dialogue(hq, db):
    marker = time.time()
    sid, comp, frames = chat_until_component(
        hq, "发起风险填报",
        f"立即调用 dispatch_risk_fill 工具，period={PERIOD}，"
        "为全部 11 家被投企业发起风险预警财务指标填报。",
        kind="risk-dispatch-confirm",
    )
    assert comp.get("interrupt_id"), "component/request 缺 interrupt_id"
    req_seq = comp["seq"]

    # 确认 → 续跑至 turn/end；风险下发事件到达
    submit_component(
        hq, sid, comp["component_id"], "submit", comp["interrupt_id"],
        values={"confirmed": True},
    )
    fill_start, _ = wait_for_event(
        hq, sid, lambda it: it["type"] == "risk/fill-start", after_seq=req_seq,
    )
    wait_for_event(hq, sid, lambda it: it["type"] == "turn/end", after_seq=req_seq)

    batch_id = fill_start["data"]["batch_id"]
    assert fill_start["data"].get("version") == 1
    assert len(fill_start["data"]["companies"]) == 11

    # 落库：批次 + 11 report + 11 待办
    assert wait_until(lambda: db_one(
        db, "SELECT status FROM risk_fill_batches WHERE batch_id=%s", (batch_id,)))
    reports = db_all(
        db,
        "SELECT company, status FROM risk_fill_reports WHERE batch_id=%s AND is_delete=false",
        (batch_id,),
    )
    assert len(reports) == 11, f"reports 应为 11 行: {len(reports)}"
    assert {r[0] for r in reports} == set(COMPANIES_11)
    assert all(r[1] == "unfilled" for r in reports)

    todos = db_all(
        db,
        "SELECT t.assignee_id, t.kind, t.status FROM biz_todos t"
        " JOIN biz_tasks k ON k.task_id=t.task_id"
        " WHERE k.scene='risk_fill' AND t.is_delete=false AND k.created_at >= to_timestamp(%s) - interval '5 seconds'",
        (marker,),
    )
    assert len(todos) >= 11, f"下发待办应 >=11: {len(todos)}"
    assert all(t[1] == "action" and t[2] == "pending" for t in todos)

    # AI 溯源：channel='tool' 且 actor 含工具名、session_id 齐、detail.context 含凭证
    rows = wait_until(lambda: [
        r for r in db_all(
            db,
            "SELECT actor, session_id::text, detail::text FROM platform_operation_logs"
            " WHERE channel='tool' AND operation='insert' AND entity LIKE '%%risk_fill%%'"
            " ORDER BY id DESC LIMIT 20",
        )
        if "dispatch_risk_fill" in (r[0] or "")
    ])
    assert rows, "缺 channel='tool' 的 dispatch_risk_fill 写日志"
    actor, log_sid, detail = rows[0]
    assert log_sid == sid, f"op-log session_id 溯源偏差: {log_sid} != {sid}"
    assert '"call_id"' in detail, "detail.context 缺 call_id（上下文凭证）"
    assert '"operator_user_id"' in detail, "detail.context 缺 operator_user_id（操作人）"
