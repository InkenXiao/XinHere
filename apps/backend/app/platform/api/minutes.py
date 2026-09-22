"""实时会议纪要 · 阶段/最终/润色 (SSE 流式) + 保存到 XuanPu 会议记录"""
import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ...core import errors
from ...persistence.models import PlatformOperationLog, SysUser
from ...persistence.session import get_db
from ...services import minutes as minutes_svc
from ...services import xuanpu as xuanpu_svc
from .deps import current_user
from .media import _identity

router = APIRouter(prefix="/minutes", tags=["minutes"])


class SummarizeIn(BaseModel):
    transcript: str
    mode: str = "final"      # stage 阶段分析 / final 最终纪要 / polish 润色
    instruction: str = ""


@router.post("/summarize")
async def summarize(body: SummarizeIn, user: SysUser = Depends(current_user)):
    mode = body.mode if body.mode in ("stage", "final", "polish") else "final"

    async def gen():
        try:
            async for delta in minutes_svc.summarize_stream(body.transcript, mode, body.instruction):
                yield {"data": json.dumps({"delta": delta}, ensure_ascii=False)}
        except Exception as exc:  # noqa: BLE001 - 以文本收尾, 前端不挂起
            yield {"data": json.dumps({"delta": f"\n[纪要生成失败] {str(exc)[:160]}"},
                                      ensure_ascii=False)}
        yield {"data": '{"done": true}'}

    return EventSourceResponse(gen())


class SaveIn(BaseModel):
    title: str = ""
    content: str
    meet_time: str = ""
    place: str = ""
    host: str = ""
    attendees: str = ""


@router.post("/save")
def save(body: SaveIn, user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    if not body.content.strip():
        raise errors.validation("纪要内容不能为空")
    title = body.title.strip() or f"实时纪要 {date.today():%Y-%m-%d}"
    try:
        result = xuanpu_svc.meeting_create(
            _identity(db, user), title=title,
            meet_date=date.today().isoformat(), meet_time=body.meet_time.strip(),
            place=body.place.strip(), host=body.host.strip(),
            attendees=body.attendees.strip(), description=body.content,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"保存会议纪要失败：{str(exc)[:160]}") from exc
    meeting = result if isinstance(result, dict) else {}
    meeting_id = meeting.get("id") or meeting.get("meeting_id") or (meeting.get("meeting") or {}).get("id")
    # 保存留痕（看板「保存的会议」计数依据；会议本体存于外部平台，本地无表）
    db.add(PlatformOperationLog(
        user_id=user.user_id, channel="page",
        actor=user.display_name or user.username,
        entity="xuanpu_meeting", operation="insert",
        record_key=str(meeting_id) if meeting_id else None,
        detail={"title": title}, entry_point="POST /api/v1/minutes/save",
    ))
    return {"ok": True, "meeting_id": meeting_id, "title": title,
            "meet_date": date.today().isoformat(), "raw": meeting}
