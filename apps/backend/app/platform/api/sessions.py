from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ...core import errors
from ...core.config import settings
from ...core.context import AuditCtx, get_ctx, reset_ctx, set_ctx
from ...persistence.models import (
    PlatformProjection,
    PlatformSession,
    PlatformSessionEvent,
    SysUser,
)
from ...persistence.session import get_db
from ...services import skills as skills_svc
from ..agent.component_handlers import HANDLERS, apply_update_draft
from ..agent.executor import executor
from ..agent.stream_bridge import bridge
from ..events.store import frame_of, store
from ..plugins.loader import plugin_set_locked
from .deps import current_user

router = APIRouter(prefix="/sessions", tags=["sessions"])

logger = logging.getLogger(__name__)


def _header(s: PlatformSession) -> dict:
    return {
        "session_id": str(s.session_id),
        "user_id": s.user_id,
        "title": s.title,
        "domain": s.domain,
        "status": s.status,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def _get_session(db: Session, session_id: str, user: SysUser) -> PlatformSession:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise errors.not_found("会话不存在")
    s = db.get(PlatformSession, sid)
    if s is None or s.user_id != user.user_id:
        raise errors.not_found("会话不存在")
    return s


def _pending_components(db: Session, session_id: str) -> list[dict]:
    sid = uuid.UUID(session_id)
    rows = db.scalars(
        select(PlatformSessionEvent)
        .where(PlatformSessionEvent.session_id == sid,
               PlatformSessionEvent.type.in_(["component/request", "component/submit"]))
        .order_by(PlatformSessionEvent.seq)
    ).all()
    open_reqs: dict[str, dict] = {}
    for r in rows:
        cid = r.data.get("component_id", "")
        if r.type == "component/request":
            open_reqs[cid] = r.data
        else:
            open_reqs.pop(cid, None)
    return [
        {"component_id": d["component_id"], "kind": d["kind"], "props": d["props"],
         "interrupt_id": d["interrupt_id"]}
        for d in open_reqs.values()
    ]


class CreateSessionIn(BaseModel):
    title: str | None = None


@router.post("")
def create_session(body: CreateSessionIn, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    plugin_set, plugin_hash = plugin_set_locked()
    s = PlatformSession(
        user_id=user.user_id, title=body.title, domain="general",
        plugin_set=plugin_set, plugin_set_hash=plugin_hash,
    )
    db.add(s)
    db.flush()
    db.refresh(s)
    return _header(s)


@router.get("")
def list_sessions(
    limit: int = Query(50, le=200), offset: int = 0,
    user: SysUser = Depends(current_user), db: Session = Depends(get_db),
):
    total = db.scalar(select(func.count()).select_from(PlatformSession).where(PlatformSession.user_id == user.user_id))
    rows = db.scalars(
        select(PlatformSession)
        .where(PlatformSession.user_id == user.user_id)
        .order_by(PlatformSession.updated_at.desc())
        .limit(limit).offset(offset)
    ).all()
    # 任务类型归类：取会话内 tool/call 工具名映射技能（历史记录按任务类型分组）
    tool_names: dict[str, list[str]] = {}
    if rows:
        tool_rows = db.execute(
            select(PlatformSessionEvent.session_id, PlatformSessionEvent.data["name"].astext)
            .where(PlatformSessionEvent.session_id.in_([s.session_id for s in rows]),
                   PlatformSessionEvent.type == "tool/call")
            .order_by(PlatformSessionEvent.session_id, PlatformSessionEvent.seq)
        ).all()
        for sid, name in tool_rows:
            tool_names.setdefault(str(sid), []).append(name)
    items = []
    for s in rows:
        last_msg = db.scalars(
            select(PlatformSessionEvent.data)
            .where(PlatformSessionEvent.session_id == s.session_id,
                   PlatformSessionEvent.type == "assistant/message")
            .order_by(PlatformSessionEvent.seq.desc())
            .limit(1)
        ).first()
        items.append({
            **_header(s),
            "last_message": (last_msg.get("content", "")[:100] if last_msg else None),
            "pending_interaction": bool(_pending_components(db, str(s.session_id))),
            "task_type": skills_svc.session_task_type(tool_names.get(str(s.session_id), [])),
        })
    return {"items": items, "total": int(total or 0)}


@router.get("/{session_id}")
def get_session(session_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    s = _get_session(db, session_id, user)
    proj = db.get(PlatformProjection, (s.session_id, "stats"))
    return {**_header(s), "stats": proj.value if proj else {}}


class ChatIn(BaseModel):
    message: str
    kb_ids: list[str] | None = None
    model: str | None = None  # 前端模型选择参数（值对 value，如 LLM）；None=默认 MAIN_MODEL


@router.post("/{session_id}/chat")
async def chat(session_id: str, body: ChatIn, request: Request,
               user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    s = _get_session(db, session_id, user)
    if s.status != "active":
        logger.info("chat 拒绝：会话已归档 sid=%s status=%s user=%s", session_id, s.status, user.username)
        raise errors.session_archived()
    if executor.get_run(session_id) is not None:
        logger.info("chat 拒绝：RUN_BUSY sid=%s user=%s", session_id, user.username)
        raise errors.run_busy()
    if not body.message.strip():
        raise errors.validation("消息不能为空")
    message = body.message
    if body.kb_ids:
        message = f"{message}\n（限定知识库：{('、'.join(body.kb_ids))}）"
    request_id = getattr(request.state, "request_id", "")

    q = bridge.subscribe(session_id)
    store.append(session_id, "user/message",
                 {"content": message, "source": "human", "version": 1})
    turn = db.scalar(
        select(func.count()).select_from(PlatformSessionEvent).where(
            PlatformSessionEvent.session_id == s.session_id,
            PlatformSessionEvent.type == "turn/start",
        )
    ) + 1
    store.append(session_id, "turn/start", {"turn": turn, "version": 1}, turn=turn)
    logger.info("chat 受理 sid=%s turn=%d user=%s kb_ids=%s model=%s msg=%.80s",
                session_id, turn, user.username, body.kb_ids, body.model, message)
    executor.start_turn(session_id, user, message, request_id, model=body.model)

    async def gen():
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield {"comment": "hb"}
                    continue
                if frame["type"] == "_marker":
                    if frame["data"].get("status") in ("done", "waiting_interrupt"):
                        break
                    continue
                yield {
                    "event": frame["type"],
                    "id": frame["id"],
                    "data": json.dumps(frame["data"], ensure_ascii=False),
                }
                if frame["type"] in ("turn/end", "error"):
                    break
        finally:
            bridge.unsubscribe(session_id, q)

    return EventSourceResponse(gen())


@router.get("/{session_id}/events")
async def get_events(
    session_id: str,
    request: Request,
    after_seq: int = -1,
    limit: int = Query(200, le=1000),
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    s = _get_session(db, session_id, user)
    accept = request.headers.get("accept", "")
    if "text/event-stream" not in accept:
        rows, has_more = store.list_events(session_id, after_seq=after_seq, limit=limit)
        return {
            "items": [
                {"seq": r.seq, "type": r.type, "time": r.time.isoformat(),
                 "data": r.data, "ignorable": r.ignorable, "turn": r.turn}
                for r in rows
            ],
            "has_more": has_more,
        }

    sid = s.session_id
    projections = {
        p.key: p.value
        for p in db.scalars(select(PlatformProjection).where(PlatformProjection.session_id == sid)).all()
    }
    pending = _pending_components(db, session_id)
    backlog, _ = store.list_events(session_id, after_seq=after_seq, limit=1000)
    q = bridge.subscribe(session_id)

    async def gen():
        try:
            yield {
                "event": "baseline",
                "id": f"{session_id}:{after_seq}",
                "data": json.dumps(
                    {"seq": after_seq, "time": s.updated_at.isoformat(),
                     "projections": projections, "pending": {"interrupts": pending}},
                    ensure_ascii=False,
                ),
            }
            for r in backlog:
                f = frame_of(r)
                yield {"event": f["type"], "id": f["id"], "data": json.dumps(f["data"], ensure_ascii=False)}
            while True:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield {"comment": "hb"}
                    continue
                if frame["type"] == "_marker":
                    continue
                yield {
                    "event": frame["type"],
                    "id": frame["id"],
                    "data": json.dumps(frame["data"], ensure_ascii=False),
                }
        finally:
            bridge.unsubscribe(session_id, q)

    return EventSourceResponse(gen())


@router.post("/{session_id}/cancel")
def cancel(session_id: str, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    _get_session(db, session_id, user)
    handle = executor.get_run(session_id)
    if handle is None:
        logger.info("cancel 空转 sid=%s user=%s", session_id, user.username)
        return {"ok": True}
    if handle.status == "waiting_interrupt" and handle.pending_component_id:
        # 等价于组件 cancel：落 cancelled 收尾 + resume，interrupt 不裸挂
        logger.info("cancel 走组件取消 sid=%s cid=%s user=%s",
                    session_id, handle.pending_component_id, user.username)
        _do_component_submit(
            db, session_id, handle.pending_component_id, "cancel", None,
            handle.pending_interrupt_id or "", user,
        )
        executor.resume(session_id, {"action": "cancelled"})
        return {"ok": True}
    logger.info("cancel 中止运行 sid=%s turn=%d user=%s", session_id, handle.turn, user.username)
    executor.cancel(session_id)
    return {"ok": True}


class FeedbackIn(BaseModel):
    text: str


@router.post("/{session_id}/feedback")
def feedback(session_id: str, body: FeedbackIn, user: SysUser = Depends(current_user),
             db: Session = Depends(get_db)):
    _get_session(db, session_id, user)
    store.append(session_id, "feedback/record", {"text": body.text, "version": 1})
    return {"ok": True}


class UpdateIn(BaseModel):
    draft: dict


@router.post("/{session_id}/components/{component_id}/update")
def component_update(session_id: str, component_id: str, body: UpdateIn,
                     user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    _get_session(db, session_id, user)
    req, _ = _find_component_request(db, session_id, component_id)
    result = apply_update_draft(db, kind=req["kind"], props=req["props"], draft=body.draft)
    if result is None:
        raise errors.validation(f"组件 {req['kind']} 不支持草稿更新")
    type_, payload = result
    seq, _ = store.append(session_id, type_, payload)
    return {"ok": True, "event_seq": seq}


class SubmitIn(BaseModel):
    action: str  # submit / cancel
    values: dict | None = None
    interrupt_id: str


@router.post("/{session_id}/components/{component_id}/submit")
def component_submit(session_id: str, component_id: str, body: SubmitIn, request: Request,
                     user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    _get_session(db, session_id, user)
    handle = executor.get_run(session_id)
    expected = handle.pending_interrupt_id if handle and handle.status == "waiting_interrupt" else None
    logger.info("component_submit 收到 sid=%s cid=%s action=%s run_status=%s expected_intr=%s user=%s",
                session_id, component_id, body.action,
                handle.status if handle else None, expected, user.username)
    event_seq, summary = _do_component_submit(
        db, session_id, component_id, body.action, body.values, body.interrupt_id, user,
        expected_interrupt_id=expected,
    )
    if handle is not None and handle.status == "waiting_interrupt":
        executor.resume(
            session_id,
            {"action": "cancelled" if body.action == "cancel" else "submit",
             "values": body.values, "summary": summary},
        )
    elif handle is None:
        # 进程重启后的孤儿 interrupt：凭 PG checkpoint 恢复 run 再 resume
        logger.info("component_submit 孤儿 interrupt 恢复 sid=%s cid=%s", session_id, component_id)
        executor.resume_detached(
            session_id, user, getattr(request.state, "request_id", ""),
            {"action": "cancelled" if body.action == "cancel" else "submit",
             "values": body.values, "summary": summary},
        )
    # submit-ack：SSE 下行确认帧，不落日志
    bridge.publish(session_id, {
        "type": "component/submit-ack", "id": f"{session_id}:{event_seq}",
        "data": {"seq": event_seq, "time": "", "component_id": component_id, "event_seq": event_seq},
    })
    return {"ok": True, "event_seq": event_seq}


def _find_component_request(db: Session, session_id: str, component_id: str) -> tuple[dict, int]:
    sid = uuid.UUID(session_id)
    rows = db.scalars(
        select(PlatformSessionEvent)
        .where(PlatformSessionEvent.session_id == sid,
               PlatformSessionEvent.type.in_(["component/request", "component/submit"]))
        .order_by(PlatformSessionEvent.seq)
    ).all()
    req = None
    req_seq = -1
    for r in rows:
        if r.data.get("component_id") != component_id:
            continue
        if r.type == "component/request":
            req, req_seq = r.data, r.seq
        else:
            raise errors.validation("组件已提交，不可重复操作")
    if req is None:
        raise errors.not_found("组件不存在")
    return req, req_seq


def _tool_origin(db: Session, session_id: str, before_seq: int) -> tuple[str, str | None]:
    """组件来源工具：component/request 之前最近的 tool/call（AI 溯源 actor/凭证）。"""
    sid = uuid.UUID(session_id)
    row = db.scalars(
        select(PlatformSessionEvent)
        .where(PlatformSessionEvent.session_id == sid,
               PlatformSessionEvent.type == "tool/call",
               PlatformSessionEvent.seq < before_seq)
        .order_by(PlatformSessionEvent.seq.desc())
        .limit(1)
    ).first()
    if row is None:
        return "component", None
    return row.data.get("name", "component"), row.data.get("call_id")


def _do_component_submit(
    db: Session, session_id: str, component_id: str, action: str, values: dict | None,
    interrupt_id: str, user: SysUser, expected_interrupt_id: str | None = None,
) -> tuple[int, str]:
    """双写铁律：① submit 事件 → ② 业务落库（失败→补偿+异常摘要）→ ③ 由调用方 resume。"""
    req, req_seq = _find_component_request(db, session_id, component_id)
    if req.get("interrupt_id") != interrupt_id:
        logger.info("component_submit 拒绝：INTERRUPT_MISMATCH sid=%s cid=%s got=%s want=%s",
                    session_id, component_id, interrupt_id, req.get("interrupt_id"))
        raise errors.interrupt_mismatch()
    if expected_interrupt_id is not None and expected_interrupt_id != interrupt_id:
        logger.info("component_submit 拒绝：run 侧 INTERRUPT_MISMATCH sid=%s cid=%s got=%s want=%s",
                    session_id, component_id, interrupt_id, expected_interrupt_id)
        raise errors.interrupt_mismatch()

    cancelled = action == "cancel"
    summary = "用户取消了本次操作" if cancelled else "已完成"
    biz_events: list[tuple[str, dict]] = []
    if not cancelled:
        handler = HANDLERS.get(req["kind"])
        if handler is None:
            raise errors.validation(f"未知组件 kind: {req['kind']}")
        # AI 溯源（红线3）：组件 confirm 的业务落库归因来源工具（channel=tool），
        # 携带会话/call_id/操作人凭证；页面 ctx 仅作 request_id/client_ip 底账
        tool_name, call_id = _tool_origin(db, session_id, req_seq)
        logger.info("component_submit 双写开始 sid=%s cid=%s kind=%s 溯源 tool=%s call_id=%s",
                    session_id, component_id, req["kind"], tool_name, call_id)
        page_ctx = get_ctx()
        audit = AuditCtx(
            user_id=user.user_id, channel="tool",
            actor=f"{settings.main_model}+{tool_name}",
            session_id=session_id, request_id=page_ctx.request_id,
            client_ip=page_ctx.client_ip,
            entry_point=f"component:{req['kind']}",
            detail_context={"context": {
                "call_id": call_id, "component_id": component_id,
                "tool_call_arguments": req["props"],
                "operator_user_id": user.user_id,
            }},
        )
        token = set_ctx(audit)
        try:
            summary, biz_events = handler(
                db, session_id=session_id, user_id=user.user_id,
                props=req["props"], values=values,
            )
            logger.info("component_submit 业务落库成功 sid=%s cid=%s kind=%s 事件=%s summary=%.80s",
                        session_id, component_id, req["kind"],
                        [t for t, _ in biz_events], summary)
        except Exception as exc:
            db.rollback()
            summary = f"提交处理异常，正在重试：{exc}"[:200]
            biz_events = []
            logger.warning("component_submit 业务落库异常 sid=%s cid=%s kind=%s err=%r",
                           session_id, component_id, req["kind"], exc)
            # 补偿记录（幂等 worker 重放，v1 记录留痕）
            from ...persistence.models import PlatformCompensation

            seq0 = store.max_seq(session_id)
            with db.begin_nested():
                db.add(PlatformCompensation(
                    session_id=uuid.UUID(session_id), event_seq=seq0 + 1,
                    plugin_name=req["kind"], action="replay_submit",
                    payload={"component_id": component_id, "props": req["props"], "values": values},
                ))
        finally:
            reset_ctx(token)

    seq, _ = store.append(session_id, "component/submit", {
        "component_id": component_id,
        "action": "cancelled" if cancelled else "submit",
        "values": values, "summary": summary, "version": 1,
    })
    for type_, payload in biz_events:
        store.append(session_id, type_, payload)
    if not cancelled and not biz_events and "异常" in summary:
        store.append(session_id, "user/message",
                     {"content": summary, "source": "inject", "version": 1})
    logger.info("component_submit 完成 sid=%s cid=%s action=%s event_seq=%d",
                session_id, component_id, "cancelled" if cancelled else "submit", seq)
    return seq, summary
