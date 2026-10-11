from __future__ import annotations

import asyncio
import json
import queue

from fastapi import APIRouter, Depends, Query

from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...persistence.models import BizTask, BizTodo, SysUser
from ...persistence.session import get_db
from ...services import todo as todo_svc
from ...services import touch as touch_svc
from ..events.todo_bus import subscribe, unsubscribe
from .deps import current_user

router = APIRouter(prefix="/todos", tags=["todos"])


@router.get("")
def list_todos(box: str = Query("assignee", pattern="^(assignee|dispatcher)$"),
               user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return {"items": todo_svc.list_todos(db, user, box)}


@router.get("/brief")
def brief(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    """今日工作要点（P5 首屏开场白）：逾期/今日到期/被退回/待审聚合，规则排序。"""
    return touch_svc.daily_brief(db, user.user_id)


@router.get("/stream")
async def todo_stream(user: SysUser = Depends(current_user)):
    """todo/changed + brief/ready 用户事件流（SSE；队列满丢最旧，前端轮询兜底）。"""
    uid = user.user_id
    q = subscribe(uid)

    async def gen():
        try:
            while True:
                try:
                    frame = await asyncio.to_thread(q.get, True, 15)
                except queue.Empty:
                    yield {"comment": "hb"}
                    continue
                yield {"event": frame["type"],
                       "data": json.dumps(frame["data"], ensure_ascii=False)}
        finally:
            unsubscribe(uid, q)

    from sse_starlette import EventSourceResponse

    return EventSourceResponse(gen())


class FeedbackIn(BaseModel):
    text: str


@router.post("/{todo_id}/feedback")
def feedback(todo_id: str, body: FeedbackIn, user: SysUser = Depends(current_user),
             db: Session = Depends(get_db)):
    return todo_svc.feedback(db, todo_id, body.text)


class NaIn(BaseModel):
    reason: str


@router.post("/{todo_id}/na")
def na(todo_id: str, body: NaIn, user: SysUser = Depends(current_user),
       db: Session = Depends(get_db)):
    return todo_svc.na(db, todo_id, body.reason)


@router.post("/{todo_id}/na-confirm")
def na_confirm(todo_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return todo_svc.na_confirm(db, todo_id)


class NaRejectIn(BaseModel):
    comment: str | None = None


@router.post("/{todo_id}/na-reject")
def na_reject(todo_id: str, body: NaRejectIn, user: SysUser = Depends(current_user),
              db: Session = Depends(get_db)):
    return todo_svc.na_reject(db, todo_id, body.comment)


@router.post("/{todo_id}/complete")
def complete(todo_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return todo_svc.complete(db, todo_id, user.user_id)


# ---------------- 任务内核：两段式审核 / 内容槽 / 催办 / 调灯 / 备注 ----------------


@router.get("/{todo_id}")
def todo_detail(todo_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    from ...core.errors import forbidden, not_found
    from ...services.common import todo_view as view

    t = db.get(BizTodo, todo_id)
    if t is None:
        raise not_found("待办不存在")
    task = db.get(BizTask, t.task_id)
    if t.assignee_id != user.user_id and (task is None or task.dispatcher_id != user.user_id):
        raise forbidden("无权查看该待办")
    return view(db, t, include_payload=True)


class PayloadIn(BaseModel):
    payload: dict


@router.put("/{todo_id}/payload")
def save_payload(todo_id: str, body: PayloadIn, user: SysUser = Depends(current_user),
                 db: Session = Depends(get_db)):
    return todo_svc.save_payload(db, todo_id, user.user_id, body.payload)


class SubmitIn(BaseModel):
    completion_text: str | None = None
    payload: dict | None = None
    session_id: str | None = None
    embed_plan: dict | None = None  # 告警填报同步填方案：{template_key?, payload}


@router.post("/{todo_id}/submit")
def submit(todo_id: str, body: SubmitIn, user: SysUser = Depends(current_user),
           db: Session = Depends(get_db)):
    import uuid as _uuid
    sid = _uuid.UUID(body.session_id) if body.session_id else None
    return todo_svc.submit(db, todo_id, user.user_id, body.completion_text, body.payload, sid,
                           embed_plan=body.embed_plan)


class ReviewIn(BaseModel):
    approve: bool
    comment: str | None = None


@router.post("/{todo_id}/review")
def review(todo_id: str, body: ReviewIn, user: SysUser = Depends(current_user),
           db: Session = Depends(get_db)):
    return todo_svc.review(db, todo_id, user.user_id, body.approve, body.comment)


@router.post("/{todo_id}/remind")
def remind(todo_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return todo_svc.remind(db, todo_id, user.user_id)


class AdjustLampIn(BaseModel):
    new_lamp: str
    reason: str = ""
    field_path: str = "overall"


@router.post("/{todo_id}/adjust-lamp")
def adjust_lamp(todo_id: str, body: AdjustLampIn, user: SysUser = Depends(current_user),
                db: Session = Depends(get_db)):
    return todo_svc.adjust_lamp(db, todo_id, user.user_id, body.new_lamp,
                                body.reason, body.field_path)


@router.get("/{todo_id}/lamp-log")
def lamp_log(todo_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    return {"items": todo_svc.lamp_log(db, todo_id)}


class RemarkIn(BaseModel):
    content: str


@router.post("/{todo_id}/remarks")
def add_todo_remark(todo_id: str, body: RemarkIn, user: SysUser = Depends(current_user),
                    db: Session = Depends(get_db)):
    return todo_svc.add_remark(db, body.content, user.user_id, todo_id=todo_id)


@router.delete("/{todo_id}/remarks/{remark_id}")
def delete_todo_remark(todo_id: str, remark_id: str, user: SysUser = Depends(current_user),
                       db: Session = Depends(get_db)):
    return todo_svc.delete_remark(db, todo_id, remark_id, user.user_id)


@router.get("/{todo_id}/remarks")
def list_todo_remarks(todo_id: str, user: SysUser = Depends(current_user),
                      db: Session = Depends(get_db)):
    return {"items": todo_svc.list_remarks(db, todo_id=todo_id)}
