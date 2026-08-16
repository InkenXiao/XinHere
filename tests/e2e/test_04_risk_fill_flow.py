"""矩阵 4：风险填报→审批（REST 确定性）。

优先复用矩阵 3 模型下发的批次（信投数科 report 仍 unfilled 的最新批次）；
不存在则 hq01 POST /risk-fills {period} 直建。
inv01 待办 → 16 项 items 原样回写（lamp 全 g）→ submit → filled
→ hq01 review 待办 → approve → reviewed → dashboard risk_board 同步。
"""
from __future__ import annotations

from .conftest import COMPANY_INV01, E2E, PERIOD, db_all, db_one, wait_until


def _pick_batch(db, hq) -> str:
    """取 3 的批次；无可用批次则 REST 直建。"""
    # 最近批次中信投数科仍未填的，视为矩阵 3 产物
    for (bid,) in db_all(db, "SELECT batch_id FROM risk_fill_batches ORDER BY created_at DESC LIMIT 5"):
        st = db_one(
            db,
            "SELECT status FROM risk_fill_reports WHERE batch_id=%s AND company=%s",
            (bid, COMPANY_INV01),
        )
        if st and st[0] == "unfilled":
            return bid
    body = hq.ok("POST", "/risk-fills", json={"period": PERIOD})
    bid = body["batch_id"]
    # REST 响应先于 commit 返回 → 等 reports 可见
    ok = wait_until(
        lambda: db_one(
            db, "SELECT status FROM risk_fill_reports WHERE batch_id=%s AND company=%s",
            (bid, COMPANY_INV01)),
    )
    assert ok, "直建批次 reports 未落库"
    return bid


def test_risk_fill_flow(hq, inv, db):
    batch_id = _pick_batch(db, hq)

    # inv01 待办出现该项
    todos = inv.ok("GET", "/todos")["items"]
    mine = [
        t for t in todos
        if t["scene"] == "risk_fill" and t["ref"].get("batch_id") == batch_id
        and t["ref"].get("company") == COMPANY_INV01
    ]
    assert mine, f"inv01 待办缺该批次项（batch={batch_id}）"
    assert mine[0]["kind"] == "action" and mine[0]["status"] == "pending"

    # 取 16 项填报单（URL 编码公司名由 httpx 处理）
    report = inv.ok("GET", f"/risk-fills/{batch_id}/reports/{COMPANY_INV01}")
    assert report["status"] == "unfilled"
    items = report["items"]
    assert len(items) == 16, f"风险指标应为 16 项: {len(items)}"
    assert any(f.get("pf") for it in items for f in it["fields"]), "应含 pf 预填只读字段"

    # 字段原样回写，lamp 全 g
    payload = {
        "items": [
            {"idx": it["idx"], "lamp": "g", "fields": [{"k": f["k"], "v": f["v"]} for f in it["fields"]]}
            for it in items
        ]
    }
    saved = inv.ok("PUT", f"/risk-fills/{batch_id}/reports/{COMPANY_INV01}/items", json=payload)
    assert len(saved["items"]) == 16
    for before, after in zip(items, saved["items"]):
        assert before["idx"] == after["idx"]
        old_pf = {f["k"]: f["v"] for f in before["fields"] if f.get("pf")}
        for f in after["fields"]:
            if f["k"] in old_pf:  # pf 只读：值保持预填
                assert f["v"] == old_pf[f["k"]] and f.get("pf")
        assert after["lamp"] == "g"
    assert saved["lamp_g"] == 16 and saved["lamp_r"] == 0 and saved["lamp_y"] == 0

    # 提交 → filled；hq01 出现 review 待办
    submitted = inv.ok("POST", f"/risk-fills/{batch_id}/reports/{COMPANY_INV01}/submit")
    assert submitted["status"] == "filled"
    review = wait_until(lambda: [
        t for t in hq.ok("GET", "/todos", params={"box": "assignee"})["items"]
        if t["kind"] == "review" and t["ref"].get("batch_id") == batch_id
        and t["ref"].get("company") == COMPANY_INV01
    ])
    assert review, "hq01 缺风险填报审批待办"

    # 审批通过 → reviewed
    reviewed = hq.ok(
        "POST", f"/risk-fills/{batch_id}/reports/{COMPANY_INV01}/review",
        json={"approve": True, "comment": f"{E2E} 审批通过"},
    )
    assert reviewed["status"] == "reviewed"

    # dashboard risk_board 同步（本批次即最新批次；等 review commit 可见）
    def _board_status():
        board = hq.ok("GET", "/dashboard/summary")["risk_board"]
        if not board or board["batch_id"] != batch_id:
            return None
        return {c["company"]: c["status"] for c in board["companies"]}.get(COMPANY_INV01)

    assert wait_until(lambda: _board_status() == "reviewed"), "risk_board 未同步 reviewed"
