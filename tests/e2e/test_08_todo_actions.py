"""矩阵 8：待办三动作（feedback→complete / na→confirm / na→reject / 忽略幂等）。

通用待办构造：HQ 无通用派发 REST 入口，矩阵允许 DB 直插 biz_tasks+biz_todos
（created_by/updated_by 走 server_default 'system'，平台红线不涉）。标题带 [e2e]。
"""
from __future__ import annotations

from .conftest import E2E, db_one, gen_id, wait_until


def _make_generic_todo(db, hq_user_id: str, inv_user_id: str, suffix: str) -> str:
    task_id, todo_id = gen_id(), gen_id()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO biz_tasks (task_id, scene, title, dispatcher_id, payload, period, status)"
            " VALUES (%s, 'generic', %s, %s, '{}'::jsonb, NULL, 'open')",
            (task_id, f"{E2E} 通用待办-{suffix}", hq_user_id),
        )
        cur.execute(
            "INSERT INTO biz_todos (todo_id, task_id, assignee_id, kind, scene, title, sub, status, ref)"
            " VALUES (%s, %s, %s, 'action', 'generic', %s, %s, 'pending', '{}'::jsonb)",
            (todo_id, task_id, inv_user_id, f"{E2E} 通用待办-{suffix}",
             f"派送人：李工 · {suffix}"),
        )
    return todo_id


def _hq_todo_by_src(hq, src_todo_id: str, kind: str, timeout: float = 10.0) -> dict | None:
    """轮询 hq01 待办（REST 响应先于 commit 返回，派生待办需等待可见）。"""
    import time

    deadline = time.monotonic() + timeout
    while True:
        items = hq.ok("GET", "/todos", params={"box": "assignee"})["items"]
        for t in items:
            if t["kind"] == kind and t["ref"].get("src_todo_id") == src_todo_id:
                return t
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.3)


def test_feedback_then_complete(hq, inv, db, hq_auth, inv_auth):
    todo_id = _make_generic_todo(db, hq_auth["user"]["user_id"], inv_auth["user"]["user_id"], "反馈链路")

    after = inv.ok("POST", f"/todos/{todo_id}/feedback", json={"text": f"{E2E} 快捷反馈：已处理"})
    assert after["status"] == "feedback_submitted"

    # 派发者 hq01 出现 feedback_review 待办
    review = _hq_todo_by_src(hq, todo_id, "feedback_review")
    assert review is not None, "hq01 缺 feedback_review 待办"

    # hq01 对原待办 complete → completed
    done = hq.ok("POST", f"/todos/{todo_id}/complete")
    assert done["status"] == "completed"
    st = wait_until(
        lambda: db_one(db, "SELECT status FROM biz_todos WHERE todo_id=%s AND status='completed'",
                       (todo_id,))
    )
    assert st, "DB 未落 completed"


def test_na_then_confirm_disappears(hq, inv, db, hq_auth, inv_auth):
    todo_id = _make_generic_todo(db, hq_auth["user"]["user_id"], inv_auth["user"]["user_id"], "不涉及确认")

    after = inv.ok("POST", f"/todos/{todo_id}/na", json={"reason": f"{E2E} 本公司不适用"})
    assert after["status"] == "na_pending"

    confirm = _hq_todo_by_src(hq, todo_id, "na_confirm")
    assert confirm is not None, "hq01 缺 na_confirm 待办"

    closed = hq.ok("POST", f"/todos/{todo_id}/na-confirm")
    assert closed["status"] == "na_closed"

    # inv01 列表不再出现（na_closed 过滤）
    inv_items = inv.ok("GET", "/todos")["items"]
    assert all(t["todo_id"] != todo_id for t in inv_items), "na_closed 待办仍在列表"


def test_na_then_reject_back_to_pending(hq, inv, db, hq_auth, inv_auth):
    todo_id = _make_generic_todo(db, hq_auth["user"]["user_id"], inv_auth["user"]["user_id"], "不涉及驳回")

    after = inv.ok("POST", f"/todos/{todo_id}/na", json={"reason": f"{E2E} 待确认"})
    assert after["status"] == "na_pending"
    assert _hq_todo_by_src(hq, todo_id, "na_confirm") is not None

    rejected = hq.ok("POST", f"/todos/{todo_id}/na-reject", json={"comment": f"{E2E} 请补充说明"})
    assert rejected["status"] == "pending"

    inv_items = inv.ok("GET", "/todos")["items"]
    back = [t for t in inv_items if t["todo_id"] == todo_id]
    assert back and back[0]["status"] == "pending", "驳回后应回 pending"


def test_ignore_is_idempotent(inv, inv_auth, db):
    # 忽略为纯前端行为：同一 pending 待办连续两次 GET 状态不变
    row = db_one(
        db,
        "SELECT todo_id FROM biz_todos WHERE assignee_id=%s AND status='pending' LIMIT 1",
        (inv_auth["user"]["user_id"],),
    )
    assert row, "inv01 无 pending 待办可供忽略幂等断言"
    todo_id = row[0]
    first = {t["todo_id"]: t["status"] for t in inv.ok("GET", "/todos")["items"]}
    second = {t["todo_id"]: t["status"] for t in inv.ok("GET", "/todos")["items"]}
    assert first.get(todo_id) == "pending" and second.get(todo_id) == "pending"
