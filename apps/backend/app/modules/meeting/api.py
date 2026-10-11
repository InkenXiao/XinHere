"""会议纪要模块 REST 与 SSE 接口。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.orm import Session


from app.platform.api.deps import current_user
from ...core import errors
from ...core.config import settings
from ...persistence.models import SysUser
from ...persistence.session import get_db
from ...platform.xuanpu import get_xuanpu_client
from . import service
from .models import MeetingRecord
from .schemas import (
    BatchDispatchIn,
    CreateMeetingIn,
    DispatchActionIn,
    MeetingDetailOut,
    MeetingHistoryItem,
    MeetingHistoryOut,
    MeetingNoteIn,
    MeetingNoteOut,
    MeetingSegmentOut,
    SpeakerRenameIn,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meeting", tags=["meeting"])


@router.post("")
async def create_meeting(
    body: CreateMeetingIn,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """创建新会议，状态为 recording。前置 Fail-Fast 校验 XuanPu 存储服务就绪状态。"""
    xuanpu = get_xuanpu_client()
    if not await xuanpu.check_health():
        raise errors.AppError("UPSTREAM_ERROR", "存储服务未就绪，请联系管理员启动 XuanPu", 503)

    rec = service.create_meeting(
        db=db,
        user_id=user.user_id,
        username=user.username,
        title=body.title,
    )
    return {
        "meeting_id": rec.meeting_id,
        "title": rec.title,
        "status": rec.status,
        "started_at": rec.started_at.isoformat(),
    }


@router.post("/{meeting_id}/segment")
async def upload_segment(
    meeting_id: str,
    file: UploadFile = File(...),
    seq_num: int = Form(...),
    duration: float = Form(5.0),
    client_id: Optional[str] = Form(None),
    start_time: Optional[float] = Form(None),
    user: SysUser = Depends(current_user),
):
    """上传 16kHz 16-bit 单声道 WAV 音频分片（VAD 变长切片），触发 ASR 转写与 15s 冷却战投分析。

    start_time 为客户端录音时间轴偏移（秒），用于字幕时间戳对齐真实录音时钟；缺省时按前序分片时长累计。
    """
    audio_bytes = await file.read()
    result = await service.handle_segment_upload(
        meeting_id=meeting_id,
        user_id=user.user_id,
        seq_num=seq_num,
        duration=duration,
        audio_bytes=audio_bytes,
        client_id=client_id,
        start_time=start_time,
    )
    return result


@router.post("/{meeting_id}/pause")
def pause_meeting(
    meeting_id: str,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """暂停会议录音。"""
    rec = service.update_meeting_status(db, meeting_id, user.user_id, "paused")
    return {"meeting_id": rec.meeting_id, "status": rec.status}


@router.post("/{meeting_id}/resume")
def resume_meeting(
    meeting_id: str,
    client_id: Optional[str] = Query(None),
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """恢复会议录音，支持附带 client_id 绑定或续期写入租约。"""
    rec = service.update_meeting_status(db, meeting_id, user.user_id, "recording", client_id=client_id)
    return {"meeting_id": rec.meeting_id, "status": rec.status}


@router.post("/{meeting_id}/heartbeat")
def meeting_heartbeat(
    meeting_id: str,
    client_id: Optional[str] = Query(None),
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """分片或前端活跃心跳，刷新最后活跃时间与租约。"""
    touched = service.touch_active_meeting(db, meeting_id, user.user_id, client_id=client_id)
    return {"meeting_id": meeting_id, "touched": touched}


@router.post("/{meeting_id}/claim-lease")
def claim_lease(
    meeting_id: str,
    client_id: str = Query(...),
    force: bool = Query(False),
    user: SysUser = Depends(current_user),
):
    """用户明确手势操作申请或接管会议写入端租约。"""
    claimed = service.claim_writer_lease(meeting_id, client_id, force=force)
    return {"meeting_id": meeting_id, "client_id": client_id, "claimed": claimed}


@router.post("/{meeting_id}/stop")
async def stop_meeting(
    meeting_id: str,
    background_tasks: BackgroundTasks,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """结束会议录音：触发后台异步 wave 合并、直传 XuanPu 归档与 LLM 最终纪要生成。支持失败重试与幂等。"""
    active_m = service.active_meetings.get(meeting_id)
    if active_m and active_m.status == "completed":
        return {"meeting_id": meeting_id, "status": "completed"}

    local_rec = db.query(MeetingRecord).filter(
        MeetingRecord.meeting_id == meeting_id,
        MeetingRecord.user_id == user.user_id,
        MeetingRecord.is_delete == False,
    ).first()
    if local_rec and local_rec.status == "completed":
        return {"meeting_id": meeting_id, "status": "completed"}

    # 幂等兜底：若本地临时记录已清理，查询 XuanPu 远端是否已归档
    if not local_rec and not active_m:
        xuanpu_m = await service.get_meeting_xuanpu(meeting_id, user.user_id, user.username)
        if xuanpu_m and xuanpu_m.get("status") == "completed":
            return {"meeting_id": meeting_id, "status": "completed"}
        raise errors.not_found("会议不存在或已归档")

    xuanpu = get_xuanpu_client()
    if not await xuanpu.check_health():
        raise errors.AppError("UPSTREAM_ERROR", "存储服务未就绪，请联系管理员启动 XuanPu", 503)

    # 关键状态同步：立即将内存态与持久态置为 processing，杜绝并发查询或即刻点看板时的状态回退
    if active_m:
        active_m.status = "processing"
    if local_rec:
        local_rec.status = "processing"
        db.commit()

    background_tasks.add_task(service.finalize_meeting_task, meeting_id, user.user_id)
    return {"meeting_id": meeting_id, "status": "processing"}


@router.post("/{meeting_id}/notes")
def add_meeting_note(
    meeting_id: str,
    body: MeetingNoteIn,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """为会议记录一条关键信息补充。"""
    note = service.add_meeting_note(
        db=db,
        meeting_id=meeting_id,
        user_id=user.user_id,
        content=body.content,
        time_sec=body.time_sec,
    )
    return note


@router.delete("/{meeting_id}/notes/{note_id}")
def delete_meeting_note(
    meeting_id: str,
    note_id: str,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """删除会议中的一条关键信息补充。"""
    ok = service.delete_meeting_note(
        db=db,
        meeting_id=meeting_id,
        user_id=user.user_id,
        note_id=note_id,
    )
    if not ok:
        raise errors.not_found("笔记不存在或已删除")
    return {"success": True, "note_id": note_id}


@router.delete("/{meeting_id}")
async def delete_meeting(
    meeting_id: str,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """删除会议纪要（优先调用 XuanPu FastMCP 软删除，并清理本地）。"""
    await service.delete_meeting_xuanpu(
        meeting_id=meeting_id,
        user_id=user.user_id,
        username=user.username,
    )
    return {"success": True, "meeting_id": meeting_id}


@router.get("/history")
async def list_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: str = Query("", description="会议标题搜索关键字"),
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """分页获取历史会议列表 (优先 XuanPu FastMCP 查询，合并进行中会议)。"""
    records, total = await service.list_meetings_xuanpu(
        user_id=user.user_id,
        username=user.username,
        page=page,
        page_size=page_size,
        keyword=keyword,
    )
    items = []
    for r in records:
        items.append(
            MeetingHistoryItem(
                meeting_id=r.get("meeting_id", ""),
                title=r.get("title", ""),
                status=r.get("status", "completed"),
                started_at=str(r.get("started_at", "")),
                duration_seconds=int(r.get("duration_seconds", 0)),
                summary=r.get("summary"),
                action_item_count=int(r.get("action_item_count", 0)),
                notes_count=int(r.get("notes_count", 0)),
                end_reason=r.get("end_reason"),
            )
        )
    return MeetingHistoryOut(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/active")
async def get_active_meeting(
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """获取当前用户正在进行中（录音/暂停/生成中）的会议，若无则返回 None。"""
    rec = service.get_active_meeting_data(db, user.user_id, user.username)
    if not rec:
        return None

    seg_outs = [
        MeetingSegmentOut(
            segment_id=str(s.get("segment_id", "")),
            meeting_id=str(s.get("meeting_id", "")),
            seq_num=int(s.get("seq_num", 0)),
            start_time=float(s.get("start_time", 0.0)),
            end_time=float(s.get("end_time", 0.0)),
            duration=float(s.get("duration", 5.0)),
            transcript=str(s.get("transcript", "")),
            speaker_id=s.get("speaker_id"),
            speaker_name=s.get("speaker_name") or "员工",
        )
        for s in (rec.get("segments") or [])
        if isinstance(s, dict)
    ]

    note_outs = [
        MeetingNoteOut(
            id=str(n.get("id")),
            time_sec=float(n.get("time_sec", 0.0)),
            content=str(n.get("content", "")),
            created_at=str(n.get("created_at", "")),
        )
        for n in (rec.get("user_notes") or [])
        if isinstance(n, dict) and "id" in n
    ]

    return MeetingDetailOut(
        meeting_id=str(rec.get("meeting_id", "")),
        user_id=str(rec.get("user_id", user.user_id)),
        username=str(rec.get("username", user.username)),
        title=str(rec.get("title", "")),
        status=str(rec.get("status", "recording")),
        started_at=str(rec.get("started_at", "")),
        ended_at=str(rec.get("ended_at")) if rec.get("ended_at") else None,
        end_reason=rec.get("end_reason"),
        last_active_at=service.format_last_active_iso(rec.get("last_active_at")),
        duration_seconds=int(rec.get("duration_seconds", 0)),
        audio_storage_key=rec.get("audio_storage_key"),
        minutes_storage_key=rec.get("minutes_storage_key"),
        summary=rec.get("summary"),
        minutes_content=rec.get("minutes_content"),
        analysis_result=rec.get("analysis_result") or {},
        action_items=rec.get("action_items") or [],
        transcript_text=rec.get("transcript_text") or "",
        user_notes=note_outs,
        segments=seg_outs,
        speakers=rec.get("speakers") or [],
    )


@router.get("/{meeting_id}")
async def get_meeting_detail(
    meeting_id: str,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """获取会议完整详情、字幕切片流、6 维分析及最终纪要 (优先 XuanPu FastMCP 查询)。"""
    rec = await service.get_meeting_xuanpu(
        meeting_id=meeting_id,
        user_id=user.user_id,
        username=user.username,
    )

    seg_outs = [
        MeetingSegmentOut(
            segment_id=str(s.get("segment_id", "")),
            meeting_id=str(s.get("meeting_id", "")),
            seq_num=int(s.get("seq_num", 0)),
            start_time=float(s.get("start_time", 0.0)),
            end_time=float(s.get("end_time", 0.0)),
            duration=float(s.get("duration", 5.0)),
            transcript=str(s.get("transcript", "")),
            speaker_id=s.get("speaker_id"),
            speaker_name=s.get("speaker_name") or "员工",
        )
        for s in (rec.get("segments") or [])
        if isinstance(s, dict)
    ]

    note_outs = [
        MeetingNoteOut(
            id=str(n.get("id")),
            time_sec=float(n.get("time_sec", 0.0)),
            content=str(n.get("content", "")),
            created_at=str(n.get("created_at", "")),
        )
        for n in (rec.get("user_notes") or [])
        if isinstance(n, dict) and "id" in n
    ]

    return MeetingDetailOut(
        meeting_id=str(rec.get("meeting_id", meeting_id)),
        user_id=str(rec.get("user_id", user.user_id)),
        username=str(rec.get("username", user.username)),
        title=str(rec.get("title", "")),
        status=str(rec.get("status", "completed")),
        started_at=str(rec.get("started_at", "")),
        ended_at=str(rec.get("ended_at")) if rec.get("ended_at") else None,
        end_reason=rec.get("end_reason"),
        last_active_at=service.format_last_active_iso(rec.get("last_active_at")),
        duration_seconds=int(rec.get("duration_seconds", 0)),
        audio_storage_key=rec.get("audio_storage_key"),
        minutes_storage_key=rec.get("minutes_storage_key"),
        summary=rec.get("summary"),
        minutes_content=rec.get("minutes_content"),
        analysis_result=rec.get("analysis_result") or {},
        action_items=rec.get("action_items") or [],
        transcript_text=rec.get("transcript_text") or "",
        user_notes=note_outs,
        segments=seg_outs,
        speakers=rec.get("speakers") or [],
    )


@router.post("/{meeting_id}/regenerate-minutes")
async def regenerate_meeting_minutes(
    meeting_id: str,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """重新生成会议纪要（针对 LLM 超时或截断导致空白纪要的会议）。"""
    rec = await service.regenerate_meeting_minutes(
        meeting_id=meeting_id,
        user_id=user.user_id,
        username=user.username,
    )

    seg_outs = [
        MeetingSegmentOut(
            segment_id=str(s.get("segment_id", "")),
            meeting_id=str(s.get("meeting_id", "")),
            seq_num=int(s.get("seq_num", 0)),
            start_time=float(s.get("start_time", 0.0)),
            end_time=float(s.get("end_time", 0.0)),
            duration=float(s.get("duration", 5.0)),
            transcript=str(s.get("transcript", "")),
            speaker_id=s.get("speaker_id"),
            speaker_name=s.get("speaker_name") or "员工",
        )
        for s in (rec.get("segments") or [])
        if isinstance(s, dict)
    ]

    note_outs = [
        MeetingNoteOut(
            id=str(n.get("id")),
            time_sec=float(n.get("time_sec", 0.0)),
            content=str(n.get("content", "")),
            created_at=str(n.get("created_at", "")),
        )
        for n in (rec.get("user_notes") or [])
        if isinstance(n, dict) and "id" in n
    ]

    return MeetingDetailOut(
        meeting_id=str(rec.get("meeting_id", meeting_id)),
        user_id=str(rec.get("user_id", user.user_id)),
        username=str(rec.get("username", user.username)),
        title=str(rec.get("title", "")),
        status=str(rec.get("status", "completed")),
        started_at=str(rec.get("started_at", "")),
        ended_at=str(rec.get("ended_at")) if rec.get("ended_at") else None,
        end_reason=rec.get("end_reason"),
        last_active_at=service.format_last_active_iso(rec.get("last_active_at")),
        duration_seconds=int(rec.get("duration_seconds", 0)),
        audio_storage_key=rec.get("audio_storage_key"),
        minutes_storage_key=rec.get("minutes_storage_key"),
        summary=rec.get("summary"),
        minutes_content=rec.get("minutes_content"),
        analysis_result=rec.get("analysis_result") or {},
        action_items=rec.get("action_items") or [],
        transcript_text=rec.get("transcript_text") or "",
        user_notes=note_outs,
        segments=seg_outs,
        speakers=rec.get("speakers") or [],
    )


@router.get("/{meeting_id}/events")
async def meeting_events_sse(
    meeting_id: str,
    request: Request,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """实时事件流（SSE）：推送切片转写、15s冷却战投动态分析及最终完成事件。"""
    # 校验归属权限
    rec = await service.get_meeting_xuanpu(meeting_id, user.user_id, user.username)
    q = service.hub.subscribe(meeting_id)

    async def event_generator() -> AsyncGenerator[dict[str, Any], None]:
        try:
            # 初始握手
            yield {
                "event": "connected",
                "data": json.dumps({"meeting_id": meeting_id}, ensure_ascii=False),
            }

            # 关键状态同步：立即下发当前最新状态，防重连/后接入客户端状态丢失
            curr_status = rec.get("status")
            if curr_status == "completed":
                yield {
                    "event": "completed",
                    "data": json.dumps({
                        "status": "completed",
                        "meeting_id": meeting_id,
                        "title": rec.get("title", ""),
                        "duration_seconds": rec.get("duration_seconds", 0),
                        "summary": rec.get("summary"),
                        "action_items": rec.get("action_items") or [],
                        "minutes_content": rec.get("minutes_content"),
                        "user_notes": rec.get("user_notes") or [],
                        "audio_url": f"/meeting/{meeting_id}/audio",
                    }, ensure_ascii=False),
                }
                return
            elif curr_status == "error":
                yield {
                    "event": "status",
                    "data": json.dumps({"status": "error", "meeting_id": meeting_id}, ensure_ascii=False),
                }
            elif curr_status in ("recording", "paused", "processing"):
                yield {
                    "event": "status",
                    "data": json.dumps({"status": curr_status, "meeting_id": meeting_id}, ensure_ascii=False),
                }

            while True:
                if await request.is_disconnected():
                    break
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield frame
                    if isinstance(frame, dict) and frame.get("event") == "completed":
                        break
                except asyncio.TimeoutError:
                    # 心跳保活
                    yield {"comment": "hb"}
        finally:
            service.hub.unsubscribe(meeting_id, q)

    return EventSourceResponse(event_generator())


@router.get("/{meeting_id}/audio")
async def stream_audio(
    meeting_id: str,
    request: Request,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """纯流式反向代理：基于 HTTP 206 Range 协议向 XuanPu 请求音频流，本地 0 音频残留。"""
    # 校验归属权限
    await service.get_meeting_xuanpu(meeting_id, user.user_id, user.username)

    xuanpu_audio_url = f"{settings.xuanpu_api_url}/api/meeting-records/{meeting_id}/audio"
    req_headers = {}
    range_header = request.headers.get("range")
    if range_header:
        req_headers["range"] = range_header
    if user and getattr(user, "username", None):
        req_headers["X-User-Name"] = quote(str(user.username).strip())

    # 1. 尝试纯流式反向代理直连 XuanPu (绕过环境代理与信任内网自签证书)
    client = httpx.AsyncClient(timeout=60.0, verify=False, trust_env=False)
    try:
        req = client.build_request("GET", xuanpu_audio_url, headers=req_headers)
        resp = await client.send(req, stream=True)
        if resp.status_code < 400:
            forward_headers = {}
            for h in ("content-range", "accept-ranges", "content-length", "content-type"):
                if h in resp.headers:
                    forward_headers[h] = resp.headers[h]

            async def _iter_stream():
                try:
                    async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
                        yield chunk
                finally:
                    await resp.aclose()
                    await client.aclose()

            return StreamingResponse(
                _iter_stream(),
                status_code=resp.status_code,
                headers=forward_headers,
                media_type=resp.headers.get("content-type", "audio/wav"),
            )
        elif resp.status_code == 416:
            forward_headers = {}
            for h in ("content-range", "accept-ranges"):
                if h in resp.headers:
                    forward_headers[h] = resp.headers[h]
            await resp.aclose()
            await client.aclose()
            return Response(
                status_code=416,
                headers=forward_headers or {"Content-Range": "bytes */0"},
            )
        elif resp.status_code == 404:
            await resp.aclose()
            await client.aclose()
            raise errors.not_found("会议音频不存在或正在处理中")
        else:
            await resp.aclose()
            await client.aclose()
            raise errors.AppError("UPSTREAM_ERROR", "存储服务未就绪，请联系管理员启动 XuanPu", 503)
    except errors.AppError:
        await client.aclose()
        raise
    except Exception as exc:
        await client.aclose()
        logger.warning("向 XuanPu 请求音频流异常: %s", exc)
        raise errors.AppError("UPSTREAM_ERROR", "存储服务未就绪，请联系管理员启动 XuanPu", 503) from exc


@router.post("/{meeting_id}/actions/{item_index}/dispatch")
async def dispatch_meeting_action(
    meeting_id: str,
    item_index: int,
    body: DispatchActionIn,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """一键派发单条会议待办事项至统一待办中心，并原子性回写 XuanPu 会议纪要。"""
    return await service.dispatch_meeting_action(
        db=db,
        meeting_id=meeting_id,
        item_index=item_index,
        data=body,
        user=user,
    )


@router.post("/{meeting_id}/actions/batch-dispatch")
async def batch_dispatch_meeting_actions(
    meeting_id: str,
    body: BatchDispatchIn,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """批量一键派发会议待办事项至统一待办中心，支持逐项状态反馈与重试。"""
    return await service.batch_dispatch_meeting_actions(
        db=db,
        meeting_id=meeting_id,
        data=body,
        user=user,
    )


@router.post("/{meeting_id}/speakers/rename")
async def rename_meeting_speaker(
    meeting_id: str,
    body: SpeakerRenameIn,
    user: SysUser = Depends(current_user),
):
    """说话人改名：把「说话人N」改名为真名/自定义名，并批量回填全部片段与转写稿。"""
    return await service.rename_speaker(
        meeting_id=meeting_id,
        user_id=user.user_id,
        username=user.username,
        label=body.label,
        name=body.name,
        resolved_user_id=body.resolved_user_id,
    )
