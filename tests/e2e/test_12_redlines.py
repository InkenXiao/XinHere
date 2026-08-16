"""矩阵 12（E2E 层可验部分）：红线。

- login 与 /auth/me 响应无 password_hash；
- 本批 op-log detail 序列化不含 'Siiit2026'；
- REST 创建的业务行 created_by/updated_at 非空，update 后 updated_at 变大（DB 触发器）；
- GET 列表产生 channel='page' 的 select 日志（条件摘要+行数）。
"""
from __future__ import annotations

import json
import time

from .conftest import (
    COMPANY_INV01, E2E, PERIOD, db_all, db_one, db_wait_one, wait_until,
)


def test_responses_never_leak_password_hash(hq, inv):
    # 重新登录会使 hq01 旧 token 逻辑失效（红线1 设计）→ 用新 token 刷新会话级 client
    r_login = hq.post("/auth/login", json={"username": "hq01", "password": "Xin@2026"})
    assert r_login.status_code == 200
    body = r_login.json()
    assert "password_hash" not in json.dumps(body), "login 响应泄露 password_hash"
    hq._c.headers["Authorization"] = f"Bearer {body['token']}"
    me_hq = hq.ok("GET", "/auth/me")
    me_inv = inv.ok("GET", "/auth/me")
    for me in (me_hq, me_inv):
        assert "password_hash" not in me and "password" not in me
        assert set(me.keys()) == {"user_id", "username", "display_name", "role", "company"}


def test_oplog_detail_has_no_secret(db):
    rows = db_all(db, "SELECT id, detail::text FROM platform_operation_logs")
    assert rows, "op-log 为空（前置用例应已产生日志）"
    leaked = [rid for rid, detail in rows if "Siiit2026" in detail]
    assert not leaked, f"op-log detail 泄露 DB 口令: ids={leaked[:5]}"


def test_business_row_audit_fields_and_trigger(hq, db, hq_auth):
    uid = hq_auth["user"]["user_id"]
    form = hq.ok("POST", "/cash-guarantees", json={"company": COMPANY_INV01, "period": PERIOD})
    fid = form["form_id"]

    row = db_wait_one(
        db,
        "SELECT created_by, updated_by, created_at, updated_at FROM cash_guarantee_reports"
        " WHERE form_id=%s",
        (fid,),
    )
    assert row is not None
    created_by, updated_by, created_at, updated_at = row
    assert created_by and created_by != "system", f"created_by 应归因操作人: {created_by}"
    assert created_by == uid and updated_by == uid
    assert created_at is not None and updated_at is not None

    time.sleep(1.1)  # 触发器 now() 粒度保险
    # POST 从上一行拷贝预填值，若恰为固定常量则 PUT 变空操作 → 先 GET 当前值再构造必然真实的变更
    cur = hq.ok("GET", f"/cash-guarantees/{fid}")
    hq.ok(
        "PUT", f"/cash-guarantees/{fid}",
        json={"avail_cash": float(cur["avail_cash"]) + 1.0, "pooled_fund": 50.0,
              "avail_credit": 200.0, "monthly_outflow": 80.0},
    )
    deadline = time.monotonic() + 10
    updated2 = updated_at
    while updated2 <= updated_at and time.monotonic() < deadline:  # 等 PUT commit 可见
        time.sleep(0.3)
        updated2 = db_one(
            db, "SELECT updated_at FROM cash_guarantee_reports WHERE form_id=%s", (fid,))[0]
    assert updated2 > updated_at, f"updated_at 触发器未生效: {updated2} <= {updated_at}"


def test_page_select_logged(hq, db):
    marker = time.time()
    hq.ok("GET", "/todos")  # GET 列表 → channel='page' select 日志
    rows = wait_until(lambda: db_all(
        db,
        "SELECT actor, entity, detail, entry_point FROM platform_operation_logs"
        " WHERE channel='page' AND operation='select' AND entity LIKE '%%biz_todos%%'"
        " AND entry_point = 'GET /api/v1/todos'"
        " AND occurred_at >= to_timestamp(%s) - interval '10 seconds'"
        " ORDER BY id DESC LIMIT 5",
        (marker,),
    ))
    assert rows, "GET /todos 未产生 channel='page' 的 select 日志"
    actor, entity, detail, entry_point = rows[0]
    assert actor, "actor 非空（页面请求归因 display_name）"
    assert "criteria" in detail and "rows" in detail, "select 日志缺条件摘要或行数"
