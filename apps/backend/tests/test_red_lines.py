from __future__ import annotations

import json
import time

from sqlalchemy import func, select

from app.persistence.models import BizTask, PlatformOperationLog


def _latest_log(db, **conds) -> PlatformOperationLog | None:
    stmt = select(PlatformOperationLog)
    for key, val in conds.items():
        col = getattr(PlatformOperationLog, key)
        stmt = stmt.where(col.contains(val) if key == "entity" else col == val)
    return db.scalars(stmt.order_by(PlatformOperationLog.id.desc())).first()


def test_red_lines(db):
    # 日志表 append-only、跨测试累积：脱敏抽查只覆盖本用例新增的日志
    log_id_from = db.scalar(select(func.coalesce(func.max(PlatformOperationLog.id), 0)))

    # ---- insert：红线2 审计字段自动填充 + insert 日志 ----
    task = BizTask(scene="generic", title="[pytest] 红线", dispatcher_id="pytest")
    db.add(task)
    db.commit()
    assert task.created_by == "pytest"
    assert task.updated_by == "pytest"
    assert task.created_at is not None and task.updated_at is not None
    first_updated = task.updated_at

    ins = _latest_log(db, entity="biz_tasks", operation="insert", user_id="pytest")
    assert ins is not None

    # ---- update：DB 触发器维护 updated_at + update 日志含变更明细 ----
    time.sleep(0.01)  # 避开 now() 同刻
    task.title = "[pytest] 红线-改"
    db.commit()
    db.refresh(task)
    assert task.updated_at > first_updated

    upd = _latest_log(db, entity="biz_tasks", operation="update", user_id="pytest")
    assert upd is not None
    assert "title" in upd.detail["changes"]

    # ---- 红线1：逻辑删除默认过滤，显式 include_deleted 可查回 ----
    task.is_delete = True
    db.commit()
    assert db.scalars(select(BizTask).where(BizTask.task_id == task.task_id)).first() is None
    revived = db.scalars(
        select(BizTask)
        .where(BizTask.task_id == task.task_id)
        .execution_options(include_deleted=True)
    ).first()
    assert revived is not None

    # ---- 红线3：select 落日志（条件摘要 + 行数） ----
    sel = _latest_log(db, entity="biz_tasks", operation="select", user_id="pytest")
    assert sel is not None
    assert sel.detail["rows"] >= 0

    # ---- 脱敏抽查：日志 detail 不含口令/敏感键值 ----
    logs = db.scalars(
        select(PlatformOperationLog).where(
            PlatformOperationLog.user_id == "pytest",
            PlatformOperationLog.id > log_id_from,
        )
    ).all()
    assert logs  # 本用例确实产生了日志
    blob = json.dumps([log.detail for log in logs], ensure_ascii=False)
    assert "Siiit2026" not in blob
    assert "password_hash" not in blob
    # teardown：task 已在用例内软删（is_delete=True），保持库干净
