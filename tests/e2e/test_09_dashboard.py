"""矩阵 9：Dashboard 对账。

GET /dashboard/summary 与 psycopg 手工聚合逐项相等。
口径已读 app/services/dashboard.py 源码确认：
- open_tasks = biz_tasks(status='open')；completed_7d = biz_todos(completed 且 7d 内更新)
- completion_rate = round(completed_total/total_todos, 4)；overdue = due<now() 且活动态
- by_scene 按 biz_tasks scene 分组（done=closed）；todo_funnel 按 status 分组排序
- risk_board = 最新风险批次；trend_14d = date(created_at)/completed 按日计数
- 业务表默认过滤 is_delete=false（ORM with_loader_criteria），SQL 侧同步补条件。
"""
from __future__ import annotations

from .conftest import db_all, db_one

ACTIVE = ("pending", "na_pending", "feedback_submitted", "submitted")


def test_dashboard_summary_reconciliation(hq, db):
    expected = {}

    expected["open_tasks"] = db_one(
        db, "SELECT count(*) FROM biz_tasks WHERE status='open' AND NOT is_delete")[0]
    expected["completed_7d"] = db_one(
        db, "SELECT count(*) FROM biz_todos WHERE status='completed'"
            " AND updated_at >= now() - interval '7 days' AND NOT is_delete")[0]
    total_todos = db_one(db, "SELECT count(*) FROM biz_todos WHERE NOT is_delete")[0]
    completed_total = db_one(
        db, "SELECT count(*) FROM biz_todos WHERE status='completed' AND NOT is_delete")[0]
    expected["completion_rate"] = round(completed_total / total_todos, 4) if total_todos else 0.0
    expected["overdue"] = db_one(
        db, "SELECT count(*) FROM biz_todos WHERE due < now()"
            " AND status = ANY(%s) AND NOT is_delete", (list(ACTIVE),))[0]

    scene_rows = db_all(
        db, "SELECT scene, status, count(*) FROM biz_tasks WHERE NOT is_delete"
            " GROUP BY scene, status")
    scene_map: dict[str, dict] = {}
    for scene, status, cnt in scene_rows:
        slot = scene_map.setdefault(scene, {"scene": scene, "total": 0, "done": 0})
        slot["total"] += cnt
        if status == "closed":
            slot["done"] += cnt
    expected["by_scene"] = scene_map

    funnel_rows = db_all(
        db, "SELECT status, count(*) FROM biz_todos WHERE NOT is_delete GROUP BY status")
    expected["todo_funnel"] = sorted((s, c) for s, c in funnel_rows)

    created_rows = dict(
        db_all(db, "SELECT date(created_at), count(*) FROM biz_todos WHERE NOT is_delete"
                   " GROUP BY date(created_at)"))
    completed_rows = dict(
        db_all(db, "SELECT date(updated_at), count(*) FROM biz_todos"
                   " WHERE status='completed' AND NOT is_delete GROUP BY date(updated_at)"))
    days = [r[0] for r in db_all(
        db, "SELECT generate_series(current_date - 13, current_date, interval '1 day')::date")]
    expected["trend_14d"] = [
        {"date": d.isoformat(), "created": int(created_rows.get(d, 0)),
         "completed": int(completed_rows.get(d, 0))}
        for d in days
    ]

    batch = db_one(
        db, "SELECT batch_id, period FROM risk_fill_batches WHERE NOT is_delete"
            " ORDER BY created_at DESC LIMIT 1")

    # ---- 接口值 ----
    s = hq.ok("GET", "/dashboard/summary")
    o = s["overview"]
    assert o["open_tasks"] == expected["open_tasks"], f"open_tasks: {o['open_tasks']} != {expected['open_tasks']}"
    assert o["completed_7d"] == expected["completed_7d"], f"completed_7d: {o['completed_7d']} != {expected['completed_7d']}"
    assert o["completion_rate"] == expected["completion_rate"], (
        f"completion_rate: {o['completion_rate']} != {expected['completion_rate']}")
    assert o["overdue"] == expected["overdue"], f"overdue: {o['overdue']} != {expected['overdue']}"

    api_scene = {x["scene"]: (x["total"], x["done"]) for x in s["by_scene"]}
    exp_scene = {k: (v["total"], v["done"]) for k, v in expected["by_scene"].items()}
    assert api_scene == exp_scene, f"by_scene 偏差: {api_scene} != {exp_scene}"

    api_funnel = sorted((x["status"], x["count"]) for x in s["todo_funnel"])
    assert api_funnel == expected["todo_funnel"], f"todo_funnel 偏差: {api_funnel} != {expected['todo_funnel']}"

    assert s["trend_14d"] == expected["trend_14d"], (
        f"trend_14d 偏差:\napi={s['trend_14d']}\nsql={expected['trend_14d']}")

    if batch is None:
        assert s["risk_board"] is None
    else:
        board = s["risk_board"]
        assert board is not None and board["batch_id"] == batch[0] and board["period"] == batch[1]
        exp_comp = dict(db_all(
            db, "SELECT company, status FROM risk_fill_reports WHERE batch_id=%s AND NOT is_delete",
            (batch[0],)))
        api_comp = {c["company"]: c["status"] for c in board["companies"]}
        assert api_comp == exp_comp, f"risk_board.companies 偏差: {api_comp} != {exp_comp}"
        lamp_rows = dict(db_all(
            db, "SELECT i.lamp, count(*) FROM risk_fill_items i"
                " JOIN risk_fill_reports r ON i.report_id = r.report_id"
                " WHERE r.batch_id=%s AND NOT i.is_delete AND NOT r.is_delete GROUP BY i.lamp",
            (batch[0],)))
        assert board["lamps"] == {
            "r": lamp_rows.get("r", 0), "y": lamp_rows.get("y", 0), "g": lamp_rows.get("g", 0)}
