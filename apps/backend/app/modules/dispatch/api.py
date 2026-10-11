"""普通待办派发 API：弹窗直派（无模板）。

落库复用 todo_svc.create_task / create_todo（与 AI do_dispatch 同一底层，
不带会话事件日志）；due 字符串转 datetime 与 POST /tasks 同规则。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session


from app.platform.api.deps import current_user

from ...core import errors
from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...services import todo as todo_svc

router = APIRouter(prefix="/todos", tags=["dispatch"])


class DispatchIn(BaseModel):
    title: str
    note: str = ""
    due: str | None = None
    # 通用派发默认必须审核，保持“提交→审核→完成”闭环。
    review_required: bool = True
    assignee_ids: list[str]


@router.post("/dispatch")
def dispatch_normal(body: DispatchIn, user: SysUser = Depends(current_user),
                    db: Session = Depends(get_db)):
    """普通待办派发（无模板）：建 generic 任务 + 每名处理人一条 action 待办。"""
    title = body.title.strip()
    if not title:
        raise errors.validation("待办标题不能为空")
    if not body.assignee_ids:
        raise errors.validation("请至少选择一名处理人")
    try:
        due = datetime.fromisoformat(body.due) if body.due else None
    except ValueError as exc:
        raise errors.validation("截止时间格式错误，应为 YYYY-MM-DD") from exc
    task = todo_svc.create_task(
        db, scene="generic", title=title, dispatcher_id=user.user_id,
        payload={"content": body.note}, review_required=True, due=due,
    )
    for uid in body.assignee_ids:
        todo_svc.create_todo(
            db, task=task, assignee_id=uid, kind="action", title=title,
            sub=body.note, ref={"source": "modal_dispatch", "content": body.note},
            due=due,
        )
    return {"task_id": task.task_id, "count": len(body.assignee_ids)}
