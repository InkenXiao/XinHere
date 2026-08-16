"""矩阵 6：KPI 族（REST 确定性）。

考核填报：hq01 建批次 → inv01 指标+里程碑拆分 → submit → hq01 review approve；
里程碑反馈：hq01 dispatch → inv01 PUT 反馈亮灯 → submit → hq01 review；
亮灯调整：hq01 POST /kpi/lamp-adjust → kpi_lamp_adjustments 行 old/new/reason/operator 齐。
"""
from __future__ import annotations

from .conftest import COMPANY_INV01, E2E, PERIOD, db_wait_one, wait_until


def test_kpi_fill_flow(hq, inv):
    batch = hq.ok("POST", "/kpi/batches", json={"period": PERIOD})
    batch_id = batch["batch_id"]
    assert batch["status"] == "collecting"

    rows = inv.ok("GET", f"/kpi/batches/{batch_id}/companies/{COMPANY_INV01}")
    indicators = rows["indicators"]
    assert len(indicators) == 4, f"KPI 指标模板 4 行: {len(indicators)}"

    # 指标填报
    put_ind = inv.ok(
        "PUT", f"/kpi/batches/{batch_id}/companies/{COMPANY_INV01}/indicators",
        json={"indicators": [
            {"indicator_id": i["indicator_id"], "content": f"{E2E} {i['name']}完成情况",
             "base_score": i["base_score"], "max_score": i["max_score"]}
            for i in indicators
        ]},
    )
    assert all(i["status"] == "filled" for i in put_ind["indicators"])

    # 里程碑拆分（对首个指标拆 2 条）
    target = indicators[0]
    put_ms = inv.ok(
        "PUT", f"/kpi/batches/{batch_id}/companies/{COMPANY_INV01}/milestones",
        json={"milestones": [
            {"indicator_id": target["indicator_id"], "content": f"{E2E} 里程碑一",
             "plan_date": "2026-08-15", "material": "周报"},
            {"indicator_id": target["indicator_id"], "content": f"{E2E} 里程碑二",
             "plan_date": "2026-08-31", "material": "月报"},
        ]},
    )
    assert len(put_ms["milestones"]) == 2

    # 提交 → hq01 审批待办 → approve → reviewed
    inv.ok("POST", f"/kpi/batches/{batch_id}/companies/{COMPANY_INV01}/submit")
    review_todo = wait_until(lambda: [
        t for t in hq.ok("GET", "/todos", params={"box": "assignee"})["items"]
        if t["kind"] == "review" and t["ref"].get("batch_id") == batch_id
        and t["ref"].get("company") == COMPANY_INV01
    ])
    assert review_todo, "hq01 缺经营者考核审批待办"
    reviewed = hq.ok(
        "POST", f"/kpi/batches/{batch_id}/companies/{COMPANY_INV01}/review",
        json={"approve": True},
    )
    assert all(i["status"] == "reviewed" for i in reviewed["indicators"])


def test_kpi_ms_feedback_flow(hq, inv, hq_auth):
    # 依赖最新考核批次（上一用例已建；其里程碑仅 2 条属信投数科）
    dispatched = hq.ok("POST", "/kpi/ms-feedbacks/dispatch", json={"period": PERIOD})
    assert dispatched["items"], "里程碑反馈下发为空"

    mine = wait_until(lambda: [
        f for f in inv.ok("GET", "/kpi/ms-feedbacks", params={"company": COMPANY_INV01})["items"]
        if f["review_status"] == "draft"
    ])
    assert mine, "inv01 无草稿态里程碑反馈"
    fb = mine[0]
    fid = fb["feedback_id"]

    saved = inv.ok(
        "PUT", f"/kpi/ms-feedbacks/{fid}",
        json={"status": "已完成", "progress": 100, "lamp": "g",
              "status_note": f"{E2E} 按期完成"},
    )
    assert saved["status"] == "已完成" and saved["progress"] == 100 and saved["lamp"] == "g"

    submitted = inv.ok("POST", f"/kpi/ms-feedbacks/{fid}/submit")
    assert submitted["review_status"] == "submitted"
    review_todo = wait_until(lambda: [
        t for t in hq.ok("GET", "/todos", params={"box": "assignee"})["items"]
        if t["kind"] == "review" and t["ref"].get("feedback_id") == fid
    ])
    assert review_todo, "hq01 缺里程碑反馈审批待办"

    reviewed = hq.ok("POST", f"/kpi/ms-feedbacks/{fid}/review", json={"approve": True})
    assert reviewed["review_status"] == "reviewed"


def test_kpi_lamp_adjust_trace(hq, db, hq_auth):
    body = hq.ok(
        "POST", "/kpi/lamp-adjust",
        json={"company": COMPANY_INV01, "indicator_name": "营业收入",
              "new_lamp": "y", "reason": f"{E2E} 亮灯调整留痕"},
    )
    assert body["new_lamp"] == "y"
    row = db_wait_one(
        db,
        "SELECT company, indicator_name, old_lamp, new_lamp, reason, operator "
        "FROM kpi_lamp_adjustments WHERE id=%s",
        (body["id"],),
    )
    assert row is not None, "kpi_lamp_adjustments 未落行"
    company, name, old_lamp, new_lamp, reason, operator = row
    assert company == COMPANY_INV01 and name == "营业收入"
    assert old_lamp in ("r", "y", "g") and new_lamp == "y"
    assert reason.startswith(E2E)
    assert operator == hq_auth["user"]["user_id"], "operator 应为 hq01 user_id"
