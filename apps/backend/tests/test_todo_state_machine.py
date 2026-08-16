from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.persistence.models import BizTodo
from app.services import todo as svc


def test_todo_state_machine(db):
    task = svc.create_task(
        db, scene="generic", title="[pytest] 状态机", dispatcher_id="pytest-disp"
    )

    # 反馈路径：pending → feedback_submitted + 分派方 feedback_review 待办
    t1 = svc.create_todo(db, task=task, assignee_id="pytest-ass")
    svc.feedback(db, t1.todo_id, "已完成反馈")
    assert t1.status == "feedback_submitted"
    review = db.scalars(
        select(BizTodo).where(
            BizTodo.task_id == task.task_id, BizTodo.kind == "feedback_review"
        )
    ).first()
    assert review is not None and review.assignee_id == "pytest-disp"
    with pytest.raises(AppError) as ei:
        svc.feedback(db, t1.todo_id, "重复反馈")
    assert ei.value.code == "VALIDATION_ERROR"

    # 不涉及路径：pending → na_pending + 分派方 na_confirm 待办 → na_closed
    t2 = svc.create_todo(db, task=task, assignee_id="pytest-ass")
    svc.na(db, t2.todo_id, "本期不涉及")
    assert t2.status == "na_pending"
    confirm = db.scalars(
        select(BizTodo).where(
            BizTodo.task_id == task.task_id, BizTodo.kind == "na_confirm"
        )
    ).first()
    assert confirm is not None and confirm.assignee_id == "pytest-disp"
    svc.na_confirm(db, t2.todo_id)
    assert t2.status == "na_closed"

    # 驳回路径：na_pending → 回 pending 且 na_comment 落库
    t3 = svc.create_todo(db, task=task, assignee_id="pytest-ass")
    svc.na(db, t3.todo_id, "先标记")
    svc.na_reject(db, t3.todo_id, "退回")
    assert t3.status == "pending"
    assert t3.na_comment == "退回"

    # 完成路径：→ completed，重复完成抛错
    svc.complete(db, t3.todo_id)
    assert t3.status == "completed"
    with pytest.raises(AppError):
        svc.complete(db, t3.todo_id)

    # teardown：软删 task 与全部 todos
    for t in db.scalars(select(BizTodo).where(BizTodo.task_id == task.task_id)).all():
        t.is_delete = True
    task.is_delete = True
    db.commit()
