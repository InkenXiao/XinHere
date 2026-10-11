"""会议纪要业务逻辑层：分片上传与 ASR 转写、单飞锁+15s冷却战投动态分析、
会后 wave 库音频合并与 LLM 最终纪要生成、MinIO 对象存储归档及 SSE 实时事件分发。
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import time
import uuid
import wave
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import desc, func, select, update
from sqlalchemy.orm import Session

from ...core import errors
from ...core.config import settings
from ...persistence.session import get_db, SessionLocal
from ...persistence.models import MeetingSpeakerAlias, SysUser
from ...platform.agent.llm import build_model
from ...platform.xuanpu import get_xuanpu_client
from ...services import storage
from ...services import todo as todo_svc
from ...services.asr import clean_asr_transcript, transcribe_audio_chunk
from ...services.identity import user_names
from ...services import voiceprint as voiceprint_svc
from .models import MeetingRecord, MeetingSegment
from .schemas import BatchDispatchIn, DispatchActionIn

logger = logging.getLogger(__name__)

# 本地标准时区：Asia/Shanghai (UTC+8)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# 存储服务异常时统一 Fail-Fast 报错文案
STORAGE_NOT_READY_MSG = "存储服务未就绪，请联系管理员启动 XuanPu"

def format_duration(seconds: int | float) -> str:
    """格式化会议时长为清晰的人性化文案，精确到秒，避免显示 0 分钟。"""
    sec = int(round(seconds))
    if sec <= 0:
        return "不足1分钟"
    if sec < 60:
        return f"{sec}秒"
    m = sec // 60
    s = sec % 60
    if s == 0:
        return f"{m}分钟"
    return f"{m}分{s}秒"


def sanitize_action_items(action_items_raw: list[Any] | None) -> list[dict[str, Any]]:
    """行动项反幻觉清洗与数据归一化。

    1. 剔除空标题或非字典项；
    2. 模糊责任人（待定/无/某人/负责人/未指定等）归一化为'未在会议中明确指定'；
    3. 模糊截止时间（待定/无/尽快/未指定等）归一化为'未明确'；
    4. 优先级收敛为 high | medium | low，默认 medium；
    5. 初始状态统一置为 pending。
    """
    if not action_items_raw or not isinstance(action_items_raw, list):
        return []

    sanitized: list[dict[str, Any]] = []
    vague_assignees = {
        "待定", "无", "某人", "负责人", "未指定", "none", "null", "谁", "未知", "相关人员", "待分配", "暂无", "待确认",
        "员工", "待指定",
    }
    vague_dues = {"待定", "无", "尽快", "未指定", "none", "null", "后续"}

    for item in action_items_raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title or title.lower() in ("none", "null"):
            continue

        assignee = str(item.get("assignee", "")).strip()
        if not assignee or assignee.lower() in vague_assignees or assignee == "员工":
            assignee = "未在会议中明确指定"

        due = str(item.get("due", "")).strip()
        if not due or due.lower() in vague_dues:
            due = "未明确"

        raw_prio = str(item.get("priority", "medium")).lower().strip()
        priority = raw_prio if raw_prio in ("high", "medium", "low") else "medium"

        row: dict[str, Any] = {
            "title": title,
            "assignee": assignee,
            "due": due,
            "priority": priority,
            "status": item.get("status") or "pending",
        }
        if "assignee_id" in item:
            row["assignee_id"] = item["assignee_id"]
        if "assignee_name" in item:
            row["assignee_name"] = item["assignee_name"]
        if "task_id" in item:
            row["task_id"] = item["task_id"]
        if "dispatched_at" in item:
            row["dispatched_at"] = item["dispatched_at"]
        sanitized.append(row)

    return sanitized


# 会议单次时长上限：3 小时（10800 秒）
MAX_MEETING_DURATION_SECONDS = 3 * 3600

# 冷却时间：15 秒
ANALYSIS_COOLDOWN_SECONDS = 15.0

# 本地临时缓存根目录
MEETING_LOCAL_DIR = Path(settings.artifact_dir or (
    Path(__file__).resolve().parents[3] / "data" / "artifacts"
)) / "meetings"
MEETING_LOCAL_DIR.mkdir(parents=True, exist_ok=True)


# ---- 内存态会议管理器 (支撑无感上水与临时状态解耦) ----
@dataclass
class ActiveMeeting:
    """会议录音中/整理中内存态，用于支撑边录边上水与实时研判，解耦物理库表"""
    meeting_id: str
    user_id: str
    username: str
    title: str
    status: str = "recording"  # recording, paused, processing, completed, error
    started_at: datetime = field(default_factory=lambda: datetime.now(SHANGHAI_TZ))
    ended_at: Optional[datetime] = None
    duration_seconds: int = 0
    audio_storage_key: Optional[str] = None
    minutes_storage_key: Optional[str] = None
    summary: Optional[str] = None
    minutes_content: Optional[str] = None
    analysis_result: dict[str, Any] = field(default_factory=dict)
    action_items: list[dict[str, Any]] = field(default_factory=list)
    transcript_text: str = ""
    user_notes: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    speakers: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    writer_client_id: Optional[str] = None
    writer_last_seen: float = 0.0
    last_active_at: float = field(default_factory=time.time)
    end_reason: Optional[str] = None


class ActiveMeetingManager:
    """内存态会议管理器：维系正在录音中或正在推理整理中的临时会议状态"""

    def __init__(self) -> None:
        self._meetings: dict[str, ActiveMeeting] = {}

    def create(self, meeting_id: str, user_id: str, username: str, title: str) -> ActiveMeeting:
        meeting = ActiveMeeting(
            meeting_id=meeting_id,
            user_id=user_id,
            username=username,
            title=title,
            status="recording",
            last_active_at=time.time(),
        )
        self._meetings[meeting_id] = meeting
        return meeting

    def get(self, meeting_id: str) -> Optional[ActiveMeeting]:
        return self._meetings.get(meeting_id)

    def get_for_user(self, meeting_id: str, user_id: str) -> Optional[ActiveMeeting]:
        m = self._meetings.get(meeting_id)
        if m and m.user_id == user_id:
            return m
        return None

    def list_for_user(self, user_id: str) -> list[ActiveMeeting]:
        return [
            m for m in self._meetings.values()
            if m.user_id == user_id and m.status in ("recording", "paused", "processing", "error")
        ]

    def list_all(self) -> list[ActiveMeeting]:
        return list(self._meetings.values())

    def remove(self, meeting_id: str) -> None:
        self._meetings.pop(meeting_id, None)


active_meetings = ActiveMeetingManager()


# ---- 内存事件广播与防重分析状态 ----
class MeetingStateHub:
    """管理活跃会议的 SSE 订阅通道与单飞锁状态。"""

    def __init__(self) -> None:
        self.subscribers: dict[str, set[asyncio.Queue]] = {}
        self.last_analysis_ts: dict[str, float] = {}
        self.analyzing_set: set[str] = set()
        self.meeting_locks: dict[str, asyncio.Lock] = {}

    def get_lock(self, meeting_id: str) -> asyncio.Lock:
        if meeting_id not in self.meeting_locks:
            self.meeting_locks[meeting_id] = asyncio.Lock()
        return self.meeting_locks[meeting_id]

    def subscribe(self, meeting_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.subscribers.setdefault(meeting_id, set()).add(q)
        return q

    def unsubscribe(self, meeting_id: str, q: asyncio.Queue) -> None:
        if meeting_id in self.subscribers:
            self.subscribers[meeting_id].discard(q)
            if not self.subscribers[meeting_id]:
                self.subscribers.pop(meeting_id, None)

    async def broadcast(self, meeting_id: str, event_type: str, data: dict[str, Any]) -> None:
        queues = list(self.subscribers.get(meeting_id, []))
        frame = {
            "event": event_type,
            "data": json.dumps(data, ensure_ascii=False),
        }
        for q in queues:
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                logger.warning("会议 %s SSE 队列溢出丢帧", meeting_id)


hub = MeetingStateHub()


def safe_create_task(coro):
    """安全创建后台任务：若无运行中事件循环（如单元测试）则优雅释放协程。"""
    try:
        loop = asyncio.get_running_loop()
        return loop.create_task(coro)
    except RuntimeError:
        coro.close()
        return None


def _get_meeting_dir(meeting_id: str) -> Path:
    d = MEETING_LOCAL_DIR / meeting_id / "segments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_minio_keys(username: str, started_at: datetime, meeting_id: str) -> tuple[str, str]:
    """生成规范存储路径：
    meetings/{username}/{YYYY}/{MM}/{DD}/{meeting_id}/audio.wav
    meetings/{username}/{YYYY}/{MM}/{DD}/{meeting_id}/minutes.md
    """
    safe_user = re.sub(r"[^\w.-]", "_", str(username or "anonymous"))
    if started_at and started_at.tzinfo:
        dt = started_at.astimezone(SHANGHAI_TZ)
    elif started_at:
        dt = started_at.replace(tzinfo=timezone.utc).astimezone(SHANGHAI_TZ)
    else:
        dt = datetime.now(SHANGHAI_TZ)

    yyyy = dt.strftime("%Y")
    mm = dt.strftime("%m")
    dd = dt.strftime("%d")
    base = f"meetings/{safe_user}/{yyyy}/{mm}/{dd}/{meeting_id}"
    return f"{base}/audio.wav", f"{base}/minutes.md"


def create_meeting(db: Session, user_id: str, username: str, title: str = "") -> MeetingRecord:
    """新建会议记录，初始状态为 recording。"""
    now = datetime.now(SHANGHAI_TZ)
    auto_title = title.strip() or f"会议研讨 {now.strftime('%Y-%m-%d %H:%M')}"
    record = MeetingRecord(
        user_id=user_id,
        username=username,
        title=auto_title,
        status="recording",
        started_at=now,
        last_active_at=now,
        end_reason=None,
        duration_seconds=0,
        analysis_result={},
        action_items=[],
        transcript_text="",
        user_notes=[],
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    # 注册进入内存态管理器
    act = active_meetings.create(
        meeting_id=record.meeting_id,
        user_id=user_id,
        username=username,
        title=auto_title,
    )
    act.last_active_at = time.time()

    # 预创建本地切片目录
    _get_meeting_dir(record.meeting_id)
    return record


def touch_active_meeting(
    db: Session,
    meeting_id: str,
    user_id: str,
    client_id: Optional[str] = None,
) -> bool:
    """刷新会议最后活跃时间戳（心跳），若带有 client_id 则同时续期写入租约。"""
    now = time.time()
    active_m = active_meetings.get(meeting_id)
    if active_m and active_m.user_id == user_id:
        active_m.last_active_at = now
        if client_id and active_m.writer_client_id == client_id:
            active_m.writer_last_seen = now
    rec = db.query(MeetingRecord).filter(
        MeetingRecord.meeting_id == meeting_id,
        MeetingRecord.user_id == user_id,
        MeetingRecord.is_delete == False,
    ).first()
    if rec:
        rec.last_active_at = datetime.now(SHANGHAI_TZ)
        db.commit()
        return True
    return bool(active_m)


def add_meeting_note(
    db: Session, meeting_id: str, user_id: str, content: str, time_sec: float
) -> dict[str, Any]:
    """为会议添加一条关键信息补充，自动附带相对时间戳并落库与广播。"""
    meeting = get_meeting(db, meeting_id, user_id)
    note_id = f"note_{uuid.uuid4().hex[:8]}"
    created_at = datetime.now(SHANGHAI_TZ).isoformat()
    note_obj = {
        "id": note_id,
        "time_sec": round(float(time_sec), 1),
        "content": content.strip(),
        "created_at": created_at,
    }
    notes = list(meeting.user_notes or [])
    notes.append(note_obj)
    meeting.user_notes = notes
    meeting.last_active_at = datetime.now(SHANGHAI_TZ)
    db.commit()
    db.refresh(meeting)

    active_m = active_meetings.get(meeting_id)
    if active_m:
        active_m.user_notes.append(note_obj)
        active_m.last_active_at = time.time()

    safe_create_task(hub.broadcast(meeting_id, "note_added", note_obj))
    return note_obj


def delete_meeting_note(
    db: Session, meeting_id: str, user_id: str, note_id: str
) -> bool:
    """删除会议中的某条关键信息补充。"""
    meeting = get_meeting(db, meeting_id, user_id)
    notes = list(meeting.user_notes or [])
    new_notes = [n for n in notes if n.get("id") != note_id]
    if len(new_notes) == len(notes):
        return False
    meeting.user_notes = new_notes
    db.commit()
    db.refresh(meeting)

    active_m = active_meetings.get(meeting_id)
    if active_m:
        active_m.user_notes = [n for n in active_m.user_notes if n.get("id") != note_id]

    safe_create_task(hub.broadcast(meeting_id, "note_deleted", {"id": note_id}))
    return True


def delete_meeting(db: Session, meeting_id: str, user_id: str) -> bool:
    """逻辑软删除会议记录及其关联分片：仅允许已完成（completed）状态。"""
    meeting = get_meeting(db, meeting_id, user_id)
    if meeting.status != "completed":
        raise errors.validation("仅已完成、已归档的会议纪要允许删除")

    meeting.is_delete = True
    db.execute(
        update(MeetingSegment)
        .where(MeetingSegment.meeting_id == meeting_id)
        .values(is_delete=True)
    )
    db.commit()
    return True




def get_meeting(db: Session, meeting_id: str, user_id: str) -> MeetingRecord:
    """获取会议，若不存在或不属于当前用户则抛出 404。"""
    stmt = select(MeetingRecord).where(
        MeetingRecord.meeting_id == meeting_id,
        MeetingRecord.is_delete == False,  # noqa: E712
    )
    m = db.scalar(stmt)
    if m is None or m.user_id != user_id:
        raise errors.not_found("会议不存在或无权访问")
    return m


def list_meetings(
    db: Session, user_id: str, page: int = 1, page_size: int = 20, keyword: str = ""
) -> tuple[list[MeetingRecord], int]:
    """分页查询用户的会议列表。"""
    stmt = select(MeetingRecord).where(
        MeetingRecord.user_id == user_id,
        MeetingRecord.is_delete == False,  # noqa: E712
    )
    if keyword:
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(MeetingRecord.title.ilike(kw))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    stmt = stmt.order_by(desc(MeetingRecord.started_at)).offset((page - 1) * page_size).limit(page_size)
    items = list(db.scalars(stmt).all())
    return items, total


def get_meeting_segments(db: Session, meeting_id: str) -> list[MeetingSegment]:
    """查询指定会议的所有切片转写记录。"""
    stmt = (
        select(MeetingSegment)
        .where(MeetingSegment.meeting_id == meeting_id, MeetingSegment.is_delete == False)  # noqa: E712
        .order_by(MeetingSegment.seq_num.asc())
    )
    return list(db.scalars(stmt).all())



async def handle_segment_upload(
    meeting_id: str,
    user_id: str,
    seq_num: int,
    duration: float,
    audio_bytes: bytes,
    client_id: Optional[str] = None,
    start_time: Optional[float] = None,
) -> dict[str, Any]:
    """处理分片上传：落盘、调用 ASR、入库、更新会议时长与全文字幕、广播 SSE、触发冷却 AI 分析。"""
    # 0. 租约校验：防止多浏览器/设备并发写入同一个会议冲突（租约有效期 15s）
    active_m = active_meetings.get(meeting_id)
    now = time.time()
    if active_m and client_id:
        if active_m.writer_client_id and (now - active_m.writer_last_seen <= 15.0):
            if active_m.writer_client_id != client_id:
                logger.warning(
                    "会议 %s 分片上传被拒绝：当前写入租约归属 %s，尝试写入端为 %s",
                    meeting_id,
                    active_m.writer_client_id,
                    client_id,
                )
                return {
                    "error": "当前会议正由其他客户端或标签页录制写入中，本端已进入旁听模式",
                    "conflict": True,
                }
        active_m.writer_client_id = client_id
        active_m.writer_last_seen = now
    if active_m:
        active_m.last_active_at = now

    # 1. 校验会议有效性
    with SessionLocal() as db:
        meeting = get_meeting(db, meeting_id, user_id)
        if meeting.status not in ("recording", "paused"):
            return {"error": f"会议当前状态为 {meeting.status}，无法接收音频分片"}

        # 校验 3 小时上限
        if meeting.duration_seconds >= MAX_MEETING_DURATION_SECONDS:
            logger.info("会议 %s 已达到 3 小时上限，自动触发结束", meeting_id)
            safe_create_task(finalize_meeting_task(meeting_id, user_id))
            return {"warning": "会议已达 3 小时上限，正在自动归档生成纪要"}

    # 2. 保存本地 WAV 文件
    meeting_dir = _get_meeting_dir(meeting_id)
    chunk_path = meeting_dir / f"{seq_num:05d}.wav"
    chunk_path.write_bytes(audio_bytes)

    # 3. 同一份音频双链路并行：ASR 转写（网关）‖ 声纹识别（本地 ONNX，线程池隔离 CPU/DB）
    #    声纹侧 fail-soft：process_meeting_audio 内部已兜底，未同意/未命中/异常返回 None → 回落「员工」
    asr_task = transcribe_audio_chunk(audio_bytes)
    vp_task = asyncio.to_thread(
        voiceprint_svc.process_meeting_audio, meeting_id, user_id, audio_bytes
    )
    cleaned_transcript, raw_transcript = await asr_task
    vp_result = await vp_task
    speaker_id: Optional[str] = vp_result.get("speaker_id")
    speaker_name: str = vp_result.get("speaker_name") or "员工"
    just_resolved_labels: list[str] = vp_result.get("just_resolved_labels") or []

    # 4. 入库（附带同 seq_num 幂等校验）
    with SessionLocal() as db:
        existing_seg = db.query(MeetingSegment).filter(
            MeetingSegment.meeting_id == meeting_id,
            MeetingSegment.seq_num == seq_num,
            MeetingSegment.is_delete == False,
        ).first()
        if existing_seg:
            logger.info("分片 %s seq=%d 已存在，返回既有分片记录", meeting_id, seq_num)
            return {
                "segment_id": existing_seg.segment_id,
                "meeting_id": meeting_id,
                "seq_num": existing_seg.seq_num,
                "start_time": existing_seg.start_time,
                "end_time": existing_seg.end_time,
                "duration": existing_seg.duration,
                "transcript": existing_seg.transcript,
                "speaker_id": existing_seg.speaker_id,
                "speaker_name": existing_seg.speaker_name or "员工",
            }

        meeting = get_meeting(db, meeting_id, user_id)
        # 时间轴：优先采用客户端上报的录音时钟偏移（前端 VAD 变长切片会截除停顿静音，
        # 累计分片时长会把时间轴压缩、与悬浮窗计时/打点对不上）；未上报时回落旧版累计算法
        if start_time is not None and start_time >= 0:
            start_time = round(float(start_time), 2)
        else:
            prev_duration = db.query(
                func.coalesce(func.sum(MeetingSegment.duration), 0.0)
            ).filter(
                MeetingSegment.meeting_id == meeting_id,
                MeetingSegment.seq_num < seq_num,
                MeetingSegment.is_delete == False,
            ).scalar() or 0.0
            start_time = round(float(prev_duration), 2)
        end_time = round(start_time + duration, 2)

        segment = MeetingSegment(
            meeting_id=meeting_id,
            seq_num=seq_num,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            transcript=cleaned_transcript,
            raw_transcript=raw_transcript,
            audio_path=str(chunk_path),
            speaker_id=speaker_id,
            speaker_name=speaker_name,
        )
        db.add(segment)

        # 累积时长与文本（格式：[时间] 说话人: 文本）
        meeting.duration_seconds = max(meeting.duration_seconds, int(round(end_time)))
        if cleaned_transcript:
            formatted_line = f"[{start_time:.0f}s] {speaker_name}: {cleaned_transcript}"
            if meeting.transcript_text:
                meeting.transcript_text += f"\n{formatted_line}"
            else:
                meeting.transcript_text = formatted_line

        meeting.last_active_at = datetime.now(SHANGHAI_TZ)
        db.commit()
        db.refresh(segment)

        seg_data = {
            "segment_id": segment.segment_id,
            "meeting_id": meeting_id,
            "seq_num": segment.seq_num,
            "start_time": segment.start_time,
            "end_time": segment.end_time,
            "duration": segment.duration,
            "transcript": segment.transcript,
            "speaker_id": segment.speaker_id,
            "speaker_name": segment.speaker_name or "员工",
        }

        active_m = active_meetings.get(meeting_id)
        if active_m:
            active_m.duration_seconds = max(active_m.duration_seconds, int(round(end_time)))
            if cleaned_transcript:
                formatted_line = f"[{start_time:.0f}s] {speaker_name}: {cleaned_transcript}"
                if active_m.transcript_text:
                    active_m.transcript_text += f"\n{formatted_line}"
                else:
                    active_m.transcript_text = formatted_line
            active_m.segments.append(seg_data)

    # 5. SSE 广播切片事件
    await hub.broadcast(meeting_id, "segment", seg_data)

    # 5.5 注册成功瞬间：本场命中实名声纹的编号历史字幕批量回填（fail-soft，含 SSE speaker_renamed）
    if just_resolved_labels:
        await backfill_resolved_speaker(meeting_id, user_id, just_resolved_labels)

    # 6. 单飞锁 + 15 秒冷却自动战投分析
    safe_create_task(trigger_dynamic_analysis_if_eligible(meeting_id, user_id))

    return seg_data


async def trigger_dynamic_analysis_if_eligible(meeting_id: str, user_id: str) -> None:
    """单飞锁 + 15 秒冷却机制：防忽悠、防重复轰炸大模型。"""
    now = time.time()
    last_ts = hub.last_analysis_ts.get(meeting_id, 0.0)

    # 未满 15 秒冷却，直接跳过
    if now - last_ts < ANALYSIS_COOLDOWN_SECONDS:
        return

    # 已有分析任务在执行中，单飞锁生效跳过
    if meeting_id in hub.analyzing_set:
        return

    hub.analyzing_set.add(meeting_id)
    hub.last_analysis_ts[meeting_id] = now

    try:
        await _execute_dynamic_analysis(meeting_id, user_id)
    finally:
        hub.analyzing_set.discard(meeting_id)


async def _execute_dynamic_analysis(meeting_id: str, user_id: str) -> None:
    """执行实时 AI 辅助分析（讨论主题、核心观点、问题/防忽悠、潜在方案、追问建议）并广播给前端。"""
    with SessionLocal() as db:
        meeting = get_meeting(db, meeting_id, user_id)
        transcript = meeting.transcript_text
        if not transcript or len(transcript.strip()) < 6:
            return

    prompt = f"""你是一名专业会议秘书与业务分析专家。
以下是一场正在进行的会议的实时发言流水（带时间戳）：
----------------
{transcript[-3000:]}
----------------

请基于上述真实讨论发言，进行实时的会议研讨提炼。务必客观、严谨、紧扣实际发言，严禁捏造未提及的事实。
请严格输出如下 JSON 格式，不得包含任何 Markdown 标记或额外解释：
{{
  "current_topic": "提炼当前正在讨论的具体主题（20字以内）",
  "core_takeaways": [
    "核心观点或关键信息1",
    "核心观点或关键信息2"
  ],
  "issues_and_risks": [
    "识别讨论中存在的问题、业务漏洞或潜在风险（如论据不充分、逻辑矛盾、风险隐患等）"
  ],
  "potential_solutions": [
    "针对上述问题的可行应对策略或潜在解决方案"
  ],
  "follow_up_questions": [
    "建议现场参会人提出的关键追问或核实问题"
  ],
  "overall": "一句话核心总结（当前会议主旨与基调，不超过40字）"
}}
"""
    try:
        loop = asyncio.get_running_loop()
        llm = build_model()
        resp = await loop.run_in_executor(None, lambda: llm.invoke(prompt))
        text = str(resp.content).strip()

        # 解析 JSON
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
        else:
            data = json.loads(text)

        # 确保关键结构与向下兼容
        data.setdefault("current_topic", "")
        data.setdefault("core_takeaways", [])
        data.setdefault("issues_and_risks", [])
        data.setdefault("potential_solutions", [])
        data.setdefault("follow_up_questions", [])
        data.setdefault("overall", data.get("current_topic", ""))
        data.setdefault("key_topics", [data["current_topic"]] if data["current_topic"] else [])
        data.setdefault("dimensions", [])
        data.setdefault("action_items", [])

        with SessionLocal() as db:
            meeting = get_meeting(db, meeting_id, user_id)
            meeting.analysis_result = data
            db.commit()

        active_m = active_meetings.get(meeting_id)
        if active_m:
            active_m.analysis_result = data

        await hub.broadcast(meeting_id, "analysis", {
            "meeting_id": meeting_id,
            "analysis": data,
            "updated_at": datetime.now(SHANGHAI_TZ).isoformat(),
        })
    except Exception as exc:
        logger.warning("动态战投看板分析失败 (meeting_id=%s): %s", meeting_id, exc)


def claim_writer_lease(meeting_id: str, client_id: str, force: bool = False) -> bool:
    """申请或接管会议写入端租约（有效期 15s），force=True 可由明确的用户手势恢复强制接管。"""
    active_m = active_meetings.get(meeting_id)
    if not active_m:
        return True
    now = time.time()
    if not force and active_m.writer_client_id and (now - active_m.writer_last_seen <= 15.0):
        if active_m.writer_client_id != client_id:
            return False
    active_m.writer_client_id = client_id
    active_m.writer_last_seen = now
    active_m.last_active_at = now
    return True


def update_meeting_status(
    db: Session,
    meeting_id: str,
    user_id: str,
    new_status: str,
    client_id: Optional[str] = None,
) -> MeetingRecord:
    """更新会议状态（暂停/继续），支持附带 client_id 同步更新写入端租约。"""
    meeting = get_meeting(db, meeting_id, user_id)
    if new_status == "paused" and meeting.status == "recording":
        meeting.status = "paused"
    elif new_status == "recording" and meeting.status == "paused":
        meeting.status = "recording"
    meeting.last_active_at = datetime.now(SHANGHAI_TZ)
    db.commit()
    db.refresh(meeting)

    active_m = active_meetings.get(meeting_id)
    if active_m:
        active_m.status = meeting.status
        active_m.last_active_at = time.time()
        if client_id:
            active_m.writer_client_id = client_id
            active_m.writer_last_seen = time.time()

    safe_create_task(hub.broadcast(meeting_id, "status", {"status": meeting.status}))
    return meeting


def merge_wav_segments(segment_paths: list[Path], output_path: Path) -> float:
    """使用标准 wave 库合并所有分片音频，返回合并后的总时长秒数。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    valid_paths = [p for p in segment_paths if p.exists() and p.stat().st_size > 44]
    if not valid_paths:
        # 空音频兜底生成静音 WAV
        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"")
        return 0.0

    total_frames = 0
    with wave.open(str(valid_paths[0]), "rb") as first_wf:
        params = first_wf.getparams()

    with wave.open(str(output_path), "wb") as out_wf:
        out_wf.setparams(params)
        for p in valid_paths:
            try:
                with wave.open(str(p), "rb") as in_wf:
                    data = in_wf.readframes(in_wf.getnframes())
                    out_wf.writeframes(data)
                    total_frames += in_wf.getnframes()
            except Exception as exc:
                logger.warning("合并分片 %s 失败: %s", p, exc)

    return float(total_frames) / float(params.framerate) if params.framerate > 0 else 0.0


async def generate_minutes_from_transcript(
    title: str,
    transcript: str,
    user_notes: list[dict[str, Any]] | None = None,
    total_duration: float = 0.0,
    started_at: datetime | None = None,
) -> tuple[str, str, list[dict[str, Any]], str]:
    """根据会议转写文本、笔记与时长，调用大模型生成标准会议纪要与结构化数据。
    返回: (final_title, summary_text, sanitized_action_items, markdown_content)"""
    # 计算时间与字数，决策纪要模板
    if started_at and started_at.tzinfo:
        started_at_local = started_at.astimezone(SHANGHAI_TZ)
    elif started_at:
        started_at_local = started_at.replace(tzinfo=timezone.utc).astimezone(SHANGHAI_TZ)
    else:
        started_at_local = datetime.now(SHANGHAI_TZ)
    time_str = started_at_local.strftime("%Y-%m-%d %H:%M:%S")
    duration_str = format_duration(total_duration)

    clean_text = re.sub(r"\[\d+s\]\s*", "", transcript or "").strip()
    total_chars = len(clean_text)
    user_notes = user_notes or []
    has_substantial_content = total_chars >= 120 or len(user_notes) >= 3

    user_notes_block = ""
    if user_notes:
        user_notes_block = "\n参会人记录的关键信息补充：\n" + "\n".join(
            [f"- [{n.get('time_sec', 0):.0f}s] {n.get('content', '')}" for n in user_notes]
        )

    loop = asyncio.get_running_loop()
    llm = build_model()

    if not has_substantial_content:
        # 采用【轻量常规会议要点纪要模板】：适合简短沟通、测试录音、文字片段少的场景
        minutes_prompt = f"""你是一名精通国资战投运营、企业经营考核与财务风控的高级会务秘书。请根据以下会议发言与关键信息补充，整理一份简明、务实、客观的常规会议要点纪要。
会议原暂定标题：{title}
会议实际发言：
----------------
{transcript if transcript else "（会议无录入音频发言）"}
----------------
{user_notes_block}

【核心原则与严防幻觉要求】：
1. 【忠于原文与错别字纠正】：语音转写可能存在同音错别字与口语冗余，请根据商业及投资语境上下文自动修正错别字，去除无意义语气词；纪要内容必须严格基于实际发言与补充笔记进行总结梳理，严谨客观，严禁捏造未提及的事实、数据或结论。
2. 【人工手记最高置信度】：参会人记录的关键信息补充拥有最高置信度，若与转录文本存在冲突或歧义，一律以参会人补充笔记为准。
3. 【空项严格抑制幻觉】：若某项内容未在会议中提及或涉及，明确注明“无”或相应缺省说明，严禁无中生有脑补推测。
4. 【发言人占位防护】：转写文本中的“员工”仅为系统占位标识，不代表任何真实姓名。提取 action_items 的 assignee 时严禁将“员工”本身作为处理人姓名填入；只能从对话内容语义中识别出的真实人名/职位作为 assignee，若无法识别出真实姓名，assignee 留空或标注“待指定”。
5. 提炼一个紧扣实际讨论内容的精炼标题 dynamic_title（不超过20字，例如“系统联调与配置核对”、“简要工作沟通”等）。

请严格输出两部分：
第一部分：一份规范的 Markdown 格式常规会议纪要，结构严格如下：
# [请替换为提炼的会议标题] — 会议纪要
> **会议时间**：{time_str} | **会议时长**：{duration_str} | **记录人**：AI 会议助手

## 一、会议核心概览 (Overview)
（简明客观陈述本次沟通的核心内容与背景，事实陈述，2-3句话）

## 二、要点与关键信息记录 (Key Information)
（根据实际发言与关键信息补充逐条梳理要点与事实）

## 三、会议决议与结论 (Resolutions & Conclusions)
（记录本次会议明确达成的一致结论或共识；若仅为常规交流测试或无明确决议，写“本次沟通无明确决议事项”）

## 四、风险与遗留问题 (Risks & Open Issues)
（若讨论中涉及潜在经营风险、财务风险或遗留待定问题则逐条列出；若无则写“无明确风险与遗留问题”）

## 五、补充事项与备忘说明 (Additional Notes)
（梳理参会人关键信息手记或备忘事项；若无补充则写“无特殊补充说明”）

## 六、待办任务与派发清单 (Tasks & Action Items)
（逐项列出明确的任务事项、处理人与建议时限；若本次沟通无明确任务安排，写“本次会议无明确任务安排待办”）

第二部分：在 Markdown 纪要结束后，请附带一个 ```json ... ``` 块，严格包含如下字段供系统解析：
```json
{{
  "dynamic_title": "提炼的精炼会议标题（不超过20字）",
  "summary": "100字以内的客观精炼摘要",
  "action_items": [
    {{"title": "任务内容", "assignee": "未在会议中明确指定", "due": "未明确", "priority": "medium"}}
  ]
}}
```
"""
    else:
        # 采用【标准深度常规会议纪要模板】：内容丰富详实，总结全面
        minutes_prompt = f"""你是一名精通国资战投运营、企业经营考核与财务风控的高级会务秘书。请根据以下会议转写全文及关键补充信息，生成一份专业、全面、结构严谨的常规会议纪要。
会议原暂定标题：{title}
会议实际发言：
----------------
{transcript if transcript else "（会议无录入音频发言）"}
----------------
{user_notes_block}

【核心原则与严防幻觉要求】：
1. 【忠于原文与错别字纠正】：语音转写可能存在同音错别字与口语冗余，请根据国资战投、企业经营考核与财务风控语境上下文自动修正错别字，去除口语化语气词；纪要内容必须严格基于实际发言与补充笔记进行总结梳理，严谨客观，严禁捏造未提及的事实、数据或结论。
2. 【人工手记最高置信度】：参会人记录的关键信息补充拥有最高置信度，若与转录文本存在冲突或歧义，一律以参会人补充笔记为准。
3. 【空项严格抑制幻觉】：若某项内容或模块在会议中未讨论涉及，明确注明“无”或“无明确风险/决议”，严禁脑补推测或套用通用模板虚构。
4. 【发言人占位防护】：转写文本中的“员工”仅为系统占位标识，不代表任何真实姓名。提取 action_items 的 assignee 时严禁将“员工”本身作为处理人姓名填入；只能从对话内容语义中识别出的真实人名/职位作为 assignee，若无法识别出真实姓名，assignee 留空或标注“待指定”。
5. 提炼一个紧扣实际讨论主题的精炼标题 dynamic_title（不超过25字，如“关于XX业务推进与技术方案研讨会议”）。

请输出两部分：
第一部分：一份完整的 Markdown 格式常规会议纪要，结构包含：
# [请替换为提炼的会议标题] — 会议纪要
> **会议时间**：{time_str} | **会议时长**：{duration_str} | **记录人**：AI 会议助手

## 一、会议核心概览 (Overview)
（梳理会议主旨、沟通背景、核心基调与整体总结，2-3段精炼阐述）

## 二、核心研讨议题与主要进展 (Key Discussions & Progress)
（按讨论模块逐一梳理各项重点议题的背景、讨论要点与具体细节）

## 三、会议决议与共识结论 (Resolutions & Conclusions)
（清晰明确地列出会议商定的一致结论、决策定论或阶段性共识）

## 四、风险与遗留问题 (Risks & Open Issues)
（逐条梳理研讨中识别出的潜在经营风控、考核短板、业务漏洞或待确认的遗留问题；若无则注明“无明确风险与遗留问题”）

## 五、补充事项与备忘说明 (Additional Notes)
（梳理参会人员手记补充、特殊说明或后续跟进备忘；若无额外事项则注明“无特殊补充说明”）

## 六、待办任务与派发清单 (Tasks & Action Items)
（逐项梳理工作任务、处理人与建议完成时限；若无任务安排则注明“本次会议无明确任务安排待办”）

第二部分：在 Markdown 纪要结束后，请附带一个 ```json ... ``` 块，严格包含如下结构供系统解析：
```json
{{
  "dynamic_title": "提炼的精炼会议标题（不超过25字）",
  "summary": "150字以内的核心纪要精简摘要",
  "action_items": [
    {{"title": "任务内容", "assignee": "处理人（未在会议中明确则填未在会议中明确指定）", "due": "建议截止时间（未明确则填未明确）", "priority": "high|medium|low"}}
  ]
}}
```
"""
    resp = await loop.run_in_executor(None, lambda: llm.invoke(minutes_prompt))
    full_result = str(resp.content).strip()

    # 深度防御：截断检测与空正文校验
    finish_reason = ""
    if hasattr(resp, "response_metadata") and isinstance(resp.response_metadata, dict):
        finish_reason = str(resp.response_metadata.get("finish_reason") or "").lower()

    if not full_result:
        logger.error(
            "大模型返回正文为空！finish_reason=%s, additional_kwargs=%s",
            finish_reason,
            getattr(resp, "additional_kwargs", {}),
        )
        if finish_reason == "length":
            raise errors.AppError("UPSTREAM_ERROR", "大模型生成因超出 Token 上限被截断，未生成正文纪要", 502)
        raise errors.AppError("UPSTREAM_ERROR", "大模型未能生成有效会议纪要正文", 502)

    # 提取 JSON 与 Markdown
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", full_result, re.DOTALL)
    summary_text = "会议已顺利结束并完成归档。"
    dynamic_title = ""
    action_items_raw = []

    trail_match = None
    if json_match:
        try:
            parsed = json.loads(json_match.group(1))
            summary_text = parsed.get("summary", summary_text)
            dynamic_title = str(parsed.get("dynamic_title", "")).strip()
            action_items_raw = parsed.get("action_items", [])
        except Exception as e:
            logger.warning("解析纪要附带 JSON 失败: %s", e)
    else:
        trail_match = re.search(r"(\{[\s\S]*\"summary\"[\s\S]*\})\s*$", full_result)
        if trail_match:
            try:
                parsed = json.loads(trail_match.group(1))
                summary_text = parsed.get("summary", summary_text)
                dynamic_title = str(parsed.get("dynamic_title", "")).strip()
                action_items_raw = parsed.get("action_items", [])
            except Exception:
                pass

    markdown_content = full_result
    if json_match:
        markdown_content = full_result[:json_match.start()].strip()
    elif trail_match:
        markdown_content = full_result[:trail_match.start()].strip()

    # 标题提取与防回落
    if dynamic_title and dynamic_title != title:
        final_title = dynamic_title
    else:
        m_title = re.search(r"^#\s+([^\n—\-]+)", markdown_content)
        if m_title and len(m_title.group(1).strip()) > 2 and "请替换为" not in m_title.group(1):
            final_title = m_title.group(1).strip()
        else:
            final_title = title

    # 行动项反幻觉清洗与归一化
    sanitized_action_items = sanitize_action_items(action_items_raw)

    return final_title, summary_text, sanitized_action_items, markdown_content


def _finalize_local_cleanup(meeting_id: str) -> None:
    """归档收尾的本地清理（幂等）：删分片记录、声纹编号向量（PIPL）与会议记录，注销内存态。"""
    with SessionLocal() as db:
        db.query(MeetingSegment).filter(MeetingSegment.meeting_id == meeting_id).delete()
        db.query(MeetingSpeakerAlias).filter(
            MeetingSpeakerAlias.meeting_id == meeting_id,
        ).update(
            {MeetingSpeakerAlias.embedding: None, MeetingSpeakerAlias.sample_seconds: 0.0},
            synchronize_session=False,
        )
        local_rec = db.query(MeetingRecord).filter(MeetingRecord.meeting_id == meeting_id).first()
        if local_rec:
            db.delete(local_rec)
        db.commit()
    active_meetings.remove(meeting_id)


async def finalize_meeting_task(meeting_id: str, user_id: str, reason: str = "manual") -> None:
    """会后完整处理流程：
    1. wave 库合并完整音频
    2. 上传完整音频至 MinIO：meetings/{username}/{YYYY}/{MM}/{DD}/{meeting_id}/audio.wav
    3. 调用 LLM 生成常规工作会议纪要与核心摘要（严防无中生有幻觉，提取动态标题）
    4. 上传 Markdown 纪要至 MinIO：meetings/{username}/{YYYY}/{MM}/{DD}/{meeting_id}/minutes.md
    5. 落库并广播 completed 事件。
    """
    lock = hub.get_lock(f"finalize_{meeting_id}")
    if lock.locked():
        logger.info("会议 %s 归档任务已在运行中，避免重复调度", meeting_id)
        return

    async with lock:
        with SessionLocal() as db:
            meeting = get_meeting(db, meeting_id, user_id)
            if meeting.status == "completed":
                return
            meeting.status = "processing"
            # ended_at/end_reason 仅空时写入：看门狗重派恢复时保留原始结束时间与原因
            if not meeting.ended_at:
                meeting.ended_at = datetime.now(SHANGHAI_TZ)
            if reason and not meeting.end_reason:
                meeting.end_reason = reason
            meeting.last_active_at = datetime.now(SHANGHAI_TZ)
            active_m = active_meetings.get(meeting_id)
            if active_m:
                active_m.status = "processing"
                if not active_m.end_reason:
                    active_m.end_reason = reason
            username = meeting.username
            started_at = meeting.started_at
            title = meeting.title
            transcript = meeting.transcript_text
            end_reason_final = meeting.end_reason or reason
            user_notes = list(meeting.user_notes or [])
            db.commit()

        await hub.broadcast(meeting_id, "status", {"status": "processing"})

        audio_key, minutes_key = _build_minio_keys(username, started_at, meeting_id)
        meeting_dir = MEETING_LOCAL_DIR / meeting_id
        meeting_dir.mkdir(parents=True, exist_ok=True)
        merged_wav_path = meeting_dir / "full_audio.wav"

        try:
            # 1. wave 合并
            segments_dir = meeting_dir / "segments"
            chunk_files = sorted(segments_dir.glob("*.wav"), key=lambda p: p.name)
            total_duration = merge_wav_segments(chunk_files, merged_wav_path)

            # 2. 并行重叠流水线：音频直传 XuanPu (MinIO 专用隔离路径) 与 LLM 结构化纪要生成并发执行
            xuanpu = get_xuanpu_client()

            async def _upload_audio_to_xuanpu() -> dict[str, Any]:
                try:
                    return await xuanpu.upload_audio_stream(
                        file_path=merged_wav_path,
                        meeting_uid=meeting_id,
                        user_name=username,
                        max_retries=2,
                    )
                except Exception as exc:
                    logger.error("音频直传 XuanPu 异常，Fail-Fast 终止归档: %s", exc)
                    raise errors.AppError("UPSTREAM_ERROR", STORAGE_NOT_READY_MSG, 503) from exc

            async def _generate_minutes_with_llm() -> tuple[str, str, list[dict[str, Any]], str]:
                try:
                    return await generate_minutes_from_transcript(
                        title=title,
                        transcript=transcript,
                        user_notes=user_notes,
                        total_duration=total_duration,
                        started_at=started_at,
                    )
                except Exception as exc:
                    logger.error("大模型生成纪要失败: %s", exc)
                    fallback_summary = "会议已顺利结束并完成归档，但纪要生成未成功（可点击重新生成）。"
                    fallback_md = (
                        f"# {title} — 会议纪要\n\n"
                        f"> **生成提示**：大模型生成会议纪要未成功（{exc}），请在详情页点击「重新生成纪要」重试。\n\n"
                    )
                    return title, fallback_summary, [], fallback_md

            # 双协程并发调度：音频上传耗时完全掩盖在大模型推理耗时内，额外等待 = 0s
            upload_result, (final_title, summary_text, sanitized_action_items, markdown_content) = (
                await asyncio.gather(_upload_audio_to_xuanpu(), _generate_minutes_with_llm())
            )


            # 若因看门狗超时自动归档，追加规范说明备注
            if reason == "timeout_recording":
                markdown_content += "\n\n> **结束说明**：因录音客户端长时间中断未收到音频输入，系统看门狗已自动归档结束本次会议并生成纪要。\n"
                if summary_text:
                    summary_text += "（因录音中断超时由系统看门狗自动归档）"
            elif reason == "timeout_paused":
                markdown_content += "\n\n> **结束说明**：因会议长时间处于暂停状态超过时限，系统看门狗已自动归档结束本次会议并生成纪要。\n"
                if summary_text:
                    summary_text += "（因会议暂停超时由系统看门狗自动归档）"

            xuanpu_audio_key = upload_result.get("object_key", "")
            xuanpu_audio_file = upload_result.get("file_name", "audio.wav")

            # 3. 收集切片与分析结果，调用 FastMCP xuanpu_meeting_save 持久化至 XuanPu
            with SessionLocal() as db:
                segs = get_meeting_segments(db, meeting_id)
                m_row = get_meeting(db, meeting_id, user_id)
                segments_payload = [
                    {
                        "segment_id": s.segment_id,
                        "meeting_id": s.meeting_id,
                        "seq_num": s.seq_num,
                        "start_time": s.start_time,
                        "end_time": s.end_time,
                        "duration": s.duration,
                        "transcript": s.transcript,
                        "speaker_id": s.speaker_id,
                        "speaker_name": getattr(s, "speaker_name", None) or "员工",
                    }
                    for s in segs
                ]
                analysis_payload = m_row.analysis_result or {}

            xuanpu_save_data = {
                "meeting_uid": meeting_id,
                "title": final_title,
                "status": "completed",
                "end_reason": end_reason_final,
                "source": "xinhere",
                "user_code": username,
                "user_name": username,
                "started_at": started_at.isoformat() if started_at else datetime.now(SHANGHAI_TZ).isoformat(),
                "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else None,
                "duration_seconds": int(round(total_duration)),
                "audio_file": xuanpu_audio_file,
                "audio_object_key": xuanpu_audio_key,
                "minutes_content": markdown_content,
                "minutes_storage_key": "",
                "summary": summary_text,
                "action_items": sanitized_action_items,
                "user_notes": user_notes,
                "analysis_result": analysis_payload,
                "transcript_text": transcript or "",
                "segments": segments_payload,
                "speakers": getattr(meeting, "speakers", []) or [],
            }

            try:
                save_res = await xuanpu.call_tool("xuanpu_meeting_save", xuanpu_save_data, user_name=username)
                if isinstance(save_res, str) and ("失败" in save_res or "Error" in save_res):
                    logger.error("XuanPu 保存会议资产报错: %s", save_res)
                    raise errors.AppError("UPSTREAM_ERROR", STORAGE_NOT_READY_MSG, 503)
                logger.info("会议 %s 成功归档持久化至 XuanPu pro_meeting_records", meeting_id)
            except errors.AppError:
                raise
            except Exception as save_err:
                logger.error("保存至 XuanPu MCP 异常，Fail-Fast 终止: %s", save_err)
                raise errors.AppError("UPSTREAM_ERROR", STORAGE_NOT_READY_MSG, 503) from save_err

            # 4. 收到成功响应后，物理清理本地临时切片与合并音频 (本地 0 音频残留)
            try:
                shutil.rmtree(meeting_dir, ignore_errors=True)
                logger.info("会议 %s 本地临时切片与合并音频已物理删除，本地 0 音频残留", meeting_id)
            except Exception as clean_err:
                logger.warning("清理本地临时切片异常: %s", clean_err)

            # 5. 清理本地临时记录与声纹编号向量 (本地 0 长期持久化；PIPL：归档后不再留存参会人声纹向量)
            #    （收尾函数内已含内存注销）
            _finalize_local_cleanup(meeting_id)

            # 7. SSE 广播完成通知
            await hub.broadcast(meeting_id, "completed", {
                "status": "completed",
                "meeting_id": meeting_id,
                "end_reason": end_reason_final,
                "title": final_title,
                "duration_seconds": int(round(total_duration)),
                "summary": summary_text,
                "action_items": sanitized_action_items,
                "minutes_content": markdown_content,
                "user_notes": user_notes,
                "audio_url": f"/meeting/{meeting_id}/audio",
            })

        except Exception as exc:
            err_msg = exc.message if isinstance(exc, errors.AppError) else (
                STORAGE_NOT_READY_MSG if "XuanPu" in str(exc) or "存储" in str(exc) else str(exc)
            )
            logger.exception("会议归档与纪要生成失败 (meeting_id=%s): %s", meeting_id, exc)
            with SessionLocal() as db:
                meeting = db.query(MeetingRecord).filter(MeetingRecord.meeting_id == meeting_id).first()
                if meeting:
                    meeting.status = "error"
                    db.commit()
            active_m = active_meetings.get(meeting_id)
            if active_m:
                active_m.status = "error"
                active_m.error = err_msg
            await hub.broadcast(meeting_id, "status", {"status": "error", "error": err_msg})


def read_meeting_audio_bytes(db: Session, meeting_id: str, user_id: str) -> bytes:
    """读取会议音频二进制流：优先从 MinIO 读取，若未完成或回落则从本地缓存读取。"""
    meeting = get_meeting(db, meeting_id, user_id)
    if meeting.audio_storage_key:
        try:
            if storage.exists(meeting.audio_storage_key):
                return storage.read(meeting.audio_storage_key)
        except Exception as e:
            logger.warning("从 MinIO 读取音频失败，尝试回落本地缓存: %s", e)

    merged_local = MEETING_LOCAL_DIR / meeting_id / "full_audio.wav"
    if merged_local.exists():
        return merged_local.read_bytes()

    raise errors.not_found("会议音频尚在处理中或未找到")


async def list_meetings_xuanpu(
    user_id: str,
    username: str,
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """从 XuanPu FastMCP 获取已归档会议资产列表，合并内存中进行中的会议。若 XuanPu 未就绪或异常则 Fail-Fast 报错。"""
    # 1. 提取内存中进行中的会议（如果有）
    in_memory_items: list[dict[str, Any]] = []
    if page == 1:
        for m in active_meetings.list_for_user(user_id):
            if not keyword or keyword.lower() in m.title.lower():
                in_memory_items.append({
                    "meeting_id": m.meeting_id,
                    "title": m.title,
                    "status": m.status,
                    "started_at": m.started_at.isoformat() if m.started_at else "",
                    "duration_seconds": m.duration_seconds,
                    "summary": m.summary,
                    "action_item_count": len(m.action_items or []),
                    "notes_count": len(m.user_notes or []),
                    "end_reason": getattr(m, "end_reason", None),
                })

    client = get_xuanpu_client()
    try:
        res = await client.call_tool(
            "xuanpu_meetings_list",
            {"keyword": keyword, "user_code": username, "source": "xinhere", "page": page, "page_size": page_size},
            user_name=username,
        )
        if isinstance(res, str):
            res = json.loads(res)
        if isinstance(res, dict) and "items" in res:
            remote_items = []
            for it in res.get("items", []):
                remote_items.append({
                    "meeting_id": it.get("meeting_uid") or it.get("meeting_id", ""),
                    "title": it.get("title", ""),
                    "status": it.get("status", "completed"),
                    "started_at": it.get("started_at") or "",
                    "duration_seconds": it.get("duration_seconds", 0),
                    "summary": it.get("summary"),
                    "action_item_count": it.get("action_item_count", len(it.get("action_items") or [])),
                    "notes_count": it.get("notes_count", len(it.get("user_notes") or [])),
                    "end_reason": it.get("end_reason"),
                })
            total = res.get("total", len(remote_items)) + len(in_memory_items)
            return in_memory_items + remote_items, total
        raise errors.AppError("UPSTREAM_ERROR", STORAGE_NOT_READY_MSG, 503)
    except errors.AppError:
        raise
    except Exception as exc:
        logger.warning("从 XuanPu 获取会议列表失败，实行 Fail-Fast: %s", exc)
        raise errors.AppError("UPSTREAM_ERROR", STORAGE_NOT_READY_MSG, 503) from exc


def format_last_active_iso(val: Any) -> Optional[str]:
    """统一将时间戳或 datetime 转为标准 ISO-8601 字符串格式。"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=SHANGHAI_TZ).isoformat()
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=SHANGHAI_TZ).isoformat()
        return val.astimezone(SHANGHAI_TZ).isoformat()
    return str(val)


async def get_meeting_xuanpu(
    meeting_id: str,
    user_id: str,
    username: str,
) -> dict[str, Any]:
    """优先查内存态与本地临时进行中会议，未命中则调用 XuanPu MCP 查询，异常实行 Fail-Fast。"""
    # 1. 优先查进行中内存态（严格校验用户权限与活跃状态）
    m = active_meetings.get(meeting_id)
    if m:
        if m.user_id != user_id:
            raise errors.not_found("会议不存在或无权访问")
        if m.status in ("recording", "paused", "processing", "error"):
            return {
                "meeting_id": m.meeting_id,
                "user_id": m.user_id,
                "username": m.username,
                "title": m.title,
                "status": m.status,
                "started_at": m.started_at.isoformat() if m.started_at else "",
                "ended_at": m.ended_at.isoformat() if m.ended_at else None,
                "duration_seconds": m.duration_seconds,
                "audio_storage_key": m.audio_storage_key,
                "minutes_storage_key": m.minutes_storage_key,
                "summary": m.summary,
                "minutes_content": m.minutes_content,
                "analysis_result": m.analysis_result or {},
                "action_items": m.action_items or [],
                "transcript_text": m.transcript_text or "",
                "user_notes": m.user_notes or [],
                "segments": m.segments or [],
                "speakers": getattr(m, "speakers", []) or [],
                "end_reason": getattr(m, "end_reason", None),
                "last_active_at": format_last_active_iso(getattr(m, "last_active_at", None)),
            }

    # 2. 查本地数据库临时活动态（防止重启/重载后内存态丢失）
    with SessionLocal() as db:
        local_rec = (
            db.query(MeetingRecord)
            .filter(
                MeetingRecord.meeting_id == meeting_id,
                MeetingRecord.user_id == user_id,
                MeetingRecord.is_delete == False,
                MeetingRecord.status.in_(["recording", "paused", "processing", "error"]),
            )
            .first()
        )
        if local_rec:
            act = active_meetings.create(local_rec.meeting_id, user_id, username, local_rec.title)
            act.status = local_rec.status
            act.started_at = local_rec.started_at
            act.ended_at = local_rec.ended_at
            act.duration_seconds = local_rec.duration_seconds or 0
            act.transcript_text = local_rec.transcript_text or ""
            act.analysis_result = local_rec.analysis_result or {}
            act.action_items = local_rec.action_items or []
            act.user_notes = list(local_rec.user_notes or [])
            act.end_reason = local_rec.end_reason
            if local_rec.last_active_at:
                act.last_active_at = (
                    local_rec.last_active_at.timestamp()
                    if hasattr(local_rec.last_active_at, "timestamp")
                    else float(local_rec.last_active_at)
                )
            segs = get_meeting_segments(db, meeting_id)
            act.segments = [
                {
                    "segment_id": s.segment_id,
                    "meeting_id": s.meeting_id,
                    "seq_num": s.seq_num,
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "duration": s.duration,
                    "transcript": s.transcript,
                    "speaker_id": s.speaker_id,
                    "speaker_name": getattr(s, "speaker_name", None) or "员工",
                }
                for s in segs
            ]
            return {
                "meeting_id": local_rec.meeting_id,
                "user_id": local_rec.user_id,
                "username": local_rec.username,
                "title": local_rec.title,
                "status": local_rec.status,
                "started_at": local_rec.started_at.isoformat() if local_rec.started_at else "",
                "ended_at": local_rec.ended_at.isoformat() if local_rec.ended_at else None,
                "duration_seconds": local_rec.duration_seconds or 0,
                "audio_storage_key": local_rec.audio_storage_key,
                "minutes_storage_key": local_rec.minutes_storage_key,
                "summary": local_rec.summary,
                "minutes_content": local_rec.minutes_content,
                "analysis_result": local_rec.analysis_result or {},
                "action_items": local_rec.action_items or [],
                "transcript_text": local_rec.transcript_text or "",
                "user_notes": local_rec.user_notes or [],
                "segments": act.segments,
                "speakers": getattr(local_rec, "speakers", []) or [],
                "end_reason": local_rec.end_reason,
                "last_active_at": format_last_active_iso(local_rec.last_active_at),
            }

    # 3. 调 XuanPu FastMCP 查询
    client = get_xuanpu_client()
    try:
        res = await client.call_tool(
            "xuanpu_meeting_get",
            {"meeting_uid": meeting_id, "user_code": username},
            user_name=username,
        )
        if isinstance(res, str):
            res = json.loads(res)
        if isinstance(res, dict) and (res.get("meeting_uid") or res.get("meeting_id")):
            rec_user = res.get("user_code") or res.get("user_name")
            if rec_user and rec_user != username:
                raise errors.not_found("会议不存在或无权访问")

            raw_segments = res.get("segments") or []
            normalized_segments = [
                {
                    **s,
                    "speaker_id": s.get("speaker_id"),
                    "speaker_name": s.get("speaker_name") or "员工",
                }
                for s in raw_segments
            ]

            raw_action_items = res.get("action_items") or []
            normalized_action_items = [
                {
                    "title": item.get("title", ""),
                    "assignee": item.get("assignee", ""),
                    "assignee_id": item.get("assignee_id"),
                    "assignee_name": item.get("assignee_name"),
                    "task_id": item.get("task_id"),
                    "dispatched_at": item.get("dispatched_at"),
                    "due": item.get("due", ""),
                    "status": item.get("status") or "pending",
                    "priority": item.get("priority", "medium"),
                }
                for item in raw_action_items
            ]

            return {
                "meeting_id": res.get("meeting_uid") or res.get("meeting_id"),
                "user_id": user_id,
                "username": res.get("user_name") or username,
                "title": res.get("title", ""),
                "status": res.get("status", "completed"),
                "started_at": res.get("started_at") or "",
                "ended_at": res.get("ended_at"),
                "duration_seconds": res.get("duration_seconds", 0),
                "audio_storage_key": res.get("audio_object_key") or res.get("audio_storage_key"),
                "minutes_storage_key": res.get("minutes_storage_key"),
                "summary": res.get("summary"),
                "minutes_content": res.get("minutes_content"),
                "analysis_result": res.get("analysis_result") or {},
                "action_items": normalized_action_items,
                "transcript_text": res.get("transcript_text") or "",
                "user_notes": res.get("user_notes") or [],
                "segments": normalized_segments,
                "speakers": res.get("speakers") or [],
                "end_reason": res.get("end_reason"),
                "last_active_at": res.get("last_active_at"),
            }
        raise errors.not_found("会议不存在或无权访问")
    except errors.AppError:
        raise
    except Exception as exc:
        err_msg = str(exc)
        if "404" in err_msg or "不存在" in err_msg:
            raise errors.not_found("会议不存在或无权访问")
        logger.warning("从 XuanPu 获取会议详情失败，实行 Fail-Fast: %s", exc)
        raise errors.AppError("UPSTREAM_ERROR", STORAGE_NOT_READY_MSG, 503) from exc


def get_active_meeting_data(db: Session, user_id: str, username: str) -> Optional[dict[str, Any]]:
    """获取用户当前正在活跃（recording, paused, processing, error）的会议详情。"""
    # 1. 优先内存态，优先最新发起的活跃会议
    candidates = active_meetings.list_for_user(user_id)
    if candidates:
        candidates.sort(
            key=lambda item: item.started_at or datetime.min.replace(tzinfo=SHANGHAI_TZ),
            reverse=True,
        )
        m = candidates[0]
        # 保证切片数据完整：若内存中 segments 缺失，从数据库补齐
        segs = list(m.segments or [])
        if not segs:
            db_segs = get_meeting_segments(db, m.meeting_id)
            if db_segs:
                segs = [
                    {
                        "segment_id": s.segment_id,
                        "meeting_id": s.meeting_id,
                        "seq_num": s.seq_num,
                        "start_time": s.start_time,
                        "end_time": s.end_time,
                        "duration": s.duration,
                        "transcript": s.transcript,
                    }
                    for s in db_segs
                ]
                m.segments = segs
        return {
            "meeting_id": m.meeting_id,
            "user_id": m.user_id,
            "username": m.username,
            "title": m.title,
            "status": m.status,
            "started_at": m.started_at.isoformat() if m.started_at else "",
            "ended_at": m.ended_at.isoformat() if m.ended_at else None,
            "duration_seconds": m.duration_seconds,
            "audio_storage_key": m.audio_storage_key,
            "minutes_storage_key": m.minutes_storage_key,
            "summary": m.summary,
            "minutes_content": m.minutes_content,
            "analysis_result": m.analysis_result or {},
            "action_items": m.action_items or [],
            "transcript_text": m.transcript_text or "",
            "user_notes": m.user_notes or [],
            "segments": segs,
            "end_reason": getattr(m, "end_reason", None),
            "last_active_at": format_last_active_iso(getattr(m, "last_active_at", None)),
        }

    # 2. 数据库检索
    rec = (
        db.query(MeetingRecord)
        .filter(
            MeetingRecord.user_id == user_id,
            MeetingRecord.status.in_(["recording", "paused", "processing", "error"]),
            MeetingRecord.is_delete == False,
        )
        .order_by(MeetingRecord.started_at.desc())
        .first()
    )
    if not rec:
        return None

    segs = get_meeting_segments(db, rec.meeting_id)
    seg_dicts = [
        {
            "segment_id": s.segment_id,
            "meeting_id": s.meeting_id,
            "seq_num": s.seq_num,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "duration": s.duration,
            "transcript": s.transcript,
        }
        for s in segs
    ]

    # 重注册进内存并完整恢复切片
    active_m = active_meetings.get(rec.meeting_id)
    if not active_m:
        active_m = active_meetings.create(rec.meeting_id, user_id, username, rec.title)
        active_m.status = rec.status
        active_m.started_at = rec.started_at
        active_m.duration_seconds = rec.duration_seconds or 0
        active_m.transcript_text = rec.transcript_text or ""
        active_m.analysis_result = rec.analysis_result or {}
        active_m.action_items = rec.action_items or []
        active_m.user_notes = list(rec.user_notes or [])
        active_m.segments = seg_dicts
        active_m.end_reason = rec.end_reason
        if rec.last_active_at:
            active_m.last_active_at = (
                rec.last_active_at.timestamp()
                if hasattr(rec.last_active_at, "timestamp")
                else float(rec.last_active_at)
            )
    elif not active_m.segments:
        active_m.segments = seg_dicts

    return {
        "meeting_id": rec.meeting_id,
        "user_id": rec.user_id,
        "username": rec.username,
        "title": rec.title,
        "status": rec.status,
        "started_at": rec.started_at.isoformat() if rec.started_at else "",
        "ended_at": rec.ended_at.isoformat() if rec.ended_at else None,
        "duration_seconds": rec.duration_seconds or 0,
        "audio_storage_key": rec.audio_storage_key,
        "minutes_storage_key": rec.minutes_storage_key,
        "summary": rec.summary,
        "minutes_content": rec.minutes_content,
        "analysis_result": rec.analysis_result or {},
        "action_items": rec.action_items or [],
        "transcript_text": rec.transcript_text or "",
        "user_notes": rec.user_notes or [],
        "segments": seg_dicts,
        "end_reason": rec.end_reason,
        "last_active_at": format_last_active_iso(rec.last_active_at),
    }


def _rewrite_transcript_speaker(transcript: str, old_label: str, new_name: str) -> str:
    """将转写全文中「[Xs] 说话人N: 」行的说话人标签替换为新名字（行级精确匹配，不误伤正文）。"""
    if not transcript:
        return transcript
    pattern = re.compile(r"^(\[\d+s\] )" + re.escape(old_label) + r"(?=: )", re.MULTILINE)
    return pattern.sub(lambda m: m.group(1) + new_name, transcript)


async def rename_speaker(
    meeting_id: str,
    user_id: str,
    username: str,
    label: str,
    name: str | None = None,
    resolved_user_id: str | None = None,
) -> dict[str, Any]:
    """说话人改名回填：把本场编号「说话人N」改名为真名/自定义名，批量回填全部片段与转写稿。

    - 进行中会议：直接更新本地分片表、transcript_text 与内存态；
    - 已归档会议：拉取 XuanPu 资产修改后整体回写（xuanpu_meeting_save 幂等覆盖）；
    - 若指定 resolved_user_id，同步绑定 MeetingSpeakerAlias，后续该编号新分片直接显示实名；
    - SSE 广播 speaker_renamed，前端实时刷新字幕。
    """
    label = (label or "").strip()
    if not label:
        raise errors.validation("说话人编号标签不能为空")
    # 绑定实名仅限本人操作（「这是我」），防止替他人覆写声纹档案
    if resolved_user_id and resolved_user_id != user_id:
        raise errors.forbidden("只能将说话人绑定到当前登录用户")

    # 最终显示名：实名优先，其次自定义文本
    final_name = (name or "").strip()
    if resolved_user_id:
        resolved_name = user_names([resolved_user_id]).get(resolved_user_id)
        if not resolved_name:
            raise errors.validation("指定的实名用户不存在")
        final_name = resolved_name
    if not final_name:
        raise errors.validation("请提供改名后的名称或绑定实名用户")
    if final_name == label:
        raise errors.validation("新名称与原编号相同，无需修改")

    # 归属校验（写操作前置）：本地会议须属主；已归档（本地无记录）须经
    # XuanPu user_code 归属校验，未通过直接抛错，禁止触碰别名/分片/内存态
    meeting_data: dict[str, Any] | None = None
    local_owned = False
    with SessionLocal() as db:
        try:
            get_meeting(db, meeting_id, user_id)
            local_owned = True
        except errors.AppError:
            local_owned = False
    if not local_owned:
        meeting_data = await get_meeting_xuanpu(meeting_id, user_id, username)

    updated_count = 0
    with SessionLocal() as db:
        # 1. 本地会议行（行锁：防与并发分片上传对 transcript_text 的读改写互相覆盖）
        meeting = None
        transcript = ""
        if local_owned:
            meeting = db.query(MeetingRecord).filter(
                MeetingRecord.meeting_id == meeting_id,
                MeetingRecord.is_delete == False,  # noqa: E712
                MeetingRecord.user_id == user_id,
            ).with_for_update().first()
            transcript = (meeting.transcript_text or "") if meeting else ""

        # 2. 绑定实名到本场编号（后续新分片直接显示实名）
        if resolved_user_id:
            alias = db.query(MeetingSpeakerAlias).filter(
                MeetingSpeakerAlias.meeting_id == meeting_id,
                MeetingSpeakerAlias.speaker_label == label,
            ).first()
            if alias is not None:
                alias.resolved_user_id = resolved_user_id
                db.commit()
                # 「这是我」顺带用该编号的远场质心重建注册档案：解决听写近场采样与会议
                # 远场拾音的信道失配（实测相似度仅 0.33~0.40，逐分片降阈值无解）
                voiceprint_svc.seed_profile_from_alias(
                    resolved_user_id, alias.embedding, float(alias.sample_seconds or 0)
                )

        # 3. 本地分片批量回填
        seg_rows = db.query(MeetingSegment).filter(
            MeetingSegment.meeting_id == meeting_id,
            MeetingSegment.speaker_name == label,
            MeetingSegment.is_delete == False,  # noqa: E712
        ).all()
        for seg in seg_rows:
            seg.speaker_name = final_name
            updated_count += 1

        # 4. 本地会议转写稿与说话人列表回填
        if meeting is not None:
            if seg_rows:
                meeting.transcript_text = _rewrite_transcript_speaker(transcript, label, final_name)
            speakers = list(getattr(meeting, "speakers", None) or [])
            for sp in speakers:
                if isinstance(sp, dict) and sp.get("speaker_name") == label:
                    sp["speaker_name"] = final_name
                    sp["resolved_user_id"] = resolved_user_id
            if seg_rows or speakers:
                meeting.speakers = speakers
            db.commit()
            db.refresh(meeting)

        # 5. 内存态同步（仅本人在录会议）
        active_m = active_meetings.get_for_user(meeting_id, user_id)
        if active_m:
            for seg in active_m.segments or []:
                if seg.get("speaker_name") == label:
                    seg["speaker_name"] = final_name
                    updated_count = max(updated_count, 1)
            active_m.transcript_text = _rewrite_transcript_speaker(
                active_m.transcript_text or "", label, final_name
            )
            for sp in active_m.speakers or []:
                if isinstance(sp, dict) and sp.get("speaker_name") == label:
                    sp["speaker_name"] = final_name
                    sp["resolved_user_id"] = resolved_user_id

    # 6. 已归档（本地无分片）：拉取 XuanPu 修改后整体回写（meeting_data 可能已在归属校验时拉取）
    local_handled = updated_count > 0
    if not local_handled:
        if meeting_data is None:
            meeting_data = await get_meeting_xuanpu(meeting_id, user_id, username)
        # 统计口径：改前命中原编号的条数（原本就叫新名的片段不计入）
        xuanpu_updated = sum(
            1 for s in (meeting_data.get("segments") or [])
            if isinstance(s, dict) and s.get("speaker_name") == label
        )
        changed = False
        for seg in meeting_data.get("segments") or []:
            if isinstance(seg, dict) and seg.get("speaker_name") == label:
                seg["speaker_name"] = final_name
                changed = True
        speakers = list(meeting_data.get("speakers") or [])
        for sp in speakers:
            if isinstance(sp, dict) and sp.get("speaker_name") == label:
                sp["speaker_name"] = final_name
                sp["resolved_user_id"] = resolved_user_id
                changed = True
        if changed:
            new_transcript = _rewrite_transcript_speaker(
                meeting_data.get("transcript_text") or "", label, final_name
            )
            client = get_xuanpu_client()
            payload = {
                "meeting_uid": meeting_id,
                "title": meeting_data.get("title") or "会议纪要",
                "status": meeting_data.get("status", "completed"),
                "end_reason": meeting_data.get("end_reason") or "manual",
                "source": "xinhere",
                "user_code": username,
                "user_name": username,
                "started_at": meeting_data.get("started_at"),
                "ended_at": meeting_data.get("ended_at"),
                "duration_seconds": meeting_data.get("duration_seconds", 0),
                "audio_file": "audio.wav",
                "audio_object_key": meeting_data.get("audio_storage_key") or "",
                "minutes_content": meeting_data.get("minutes_content") or "",
                "minutes_storage_key": "",
                "summary": meeting_data.get("summary") or "",
                "action_items": meeting_data.get("action_items") or [],
                "user_notes": meeting_data.get("user_notes") or [],
                "analysis_result": meeting_data.get("analysis_result") or {},
                "transcript_text": new_transcript,
                "segments": meeting_data.get("segments") or [],
                "speakers": speakers,
            }
            try:
                res = await client.call_tool("xuanpu_meeting_save", payload, user_name=username)
                if isinstance(res, str) and ("失败" in res or "Error" in res):
                    raise errors.AppError("UPSTREAM_ERROR", STORAGE_NOT_READY_MSG, 503)
            except errors.AppError:
                raise
            except Exception as exc:
                logger.error("说话人改名回写 XuanPu 失败 meeting=%s: %s", meeting_id, exc)
                raise errors.AppError("UPSTREAM_ERROR", STORAGE_NOT_READY_MSG, 503) from exc
            updated_count = xuanpu_updated

    # 7. SSE 广播实时刷新
    await hub.broadcast(meeting_id, "speaker_renamed", {
        "meeting_id": meeting_id,
        "from": label,
        "to": final_name,
        "resolved_user_id": resolved_user_id,
        "updated": updated_count,
    })

    return {
        "meeting_id": meeting_id,
        "from": label,
        "to": final_name,
        "resolved_user_id": resolved_user_id,
        "updated": updated_count,
    }


async def backfill_resolved_speaker(meeting_id: str, user_id: str, labels: list[str]) -> int:
    """注册成功后自动实名回填：无感采样达标注册成功的瞬间，把本场命中实名声纹的
    编号（说话人N）的历史字幕/转写稿/说话人列表批量回填为实名，并广播 SSE。

    与 rename_speaker 本地路径同链路（分片表 + transcript_text + speakers + 内存态 + SSE）；
    仅处理本地在档会议（调用方 handle_segment_upload 已校验属主），已归档会议不回溯
    （编号仅本场有效）。fail-soft：异常记日志返回 0，不阻断分片上传主流程。
    返回回填分片数。
    """
    labels = [lb for lb in (labels or []) if lb]
    if not labels:
        return 0
    try:
        name = user_names([user_id]).get(user_id) or user_id
        updated_count = 0
        with SessionLocal() as db:
            meeting = db.query(MeetingRecord).filter(
                MeetingRecord.meeting_id == meeting_id,
                MeetingRecord.is_delete == False,  # noqa: E712
            ).with_for_update().first()
            transcript = (meeting.transcript_text or "") if meeting else ""
            for label in labels:
                seg_rows = db.query(MeetingSegment).filter(
                    MeetingSegment.meeting_id == meeting_id,
                    MeetingSegment.speaker_name == label,
                    MeetingSegment.is_delete == False,  # noqa: E712
                ).all()
                for seg in seg_rows:
                    seg.speaker_name = name
                    updated_count += 1
                if meeting is not None:
                    if seg_rows:
                        meeting.transcript_text = _rewrite_transcript_speaker(transcript, label, name)
                    speakers = list(getattr(meeting, "speakers", None) or [])
                    speakers_changed = False
                    for sp in speakers:
                        if isinstance(sp, dict) and sp.get("speaker_name") == label:
                            sp["speaker_name"] = name
                            sp["resolved_user_id"] = user_id
                            speakers_changed = True
                    if seg_rows or speakers_changed:
                        meeting.speakers = speakers
            db.commit()

            # 内存态同步（分片上传者即会议属主）
            active_m = active_meetings.get_for_user(meeting_id, user_id)
            if active_m:
                for seg in active_m.segments or []:
                    if seg.get("speaker_name") in labels:
                        seg["speaker_name"] = name
                for label in labels:
                    active_m.transcript_text = _rewrite_transcript_speaker(
                        active_m.transcript_text or "", label, name
                    )
                for sp in active_m.speakers or []:
                    if isinstance(sp, dict) and sp.get("speaker_name") in labels:
                        sp["speaker_name"] = name
                        sp["resolved_user_id"] = user_id

        for label in labels:
            await hub.broadcast(meeting_id, "speaker_renamed", {
                "meeting_id": meeting_id,
                "from": label,
                "to": name,
                "resolved_user_id": user_id,
                "updated": updated_count,
            })
        return updated_count
    except Exception:
        logger.warning(
            "会议编号自动实名回填失败 meeting=%s uid=%s labels=%s",
            meeting_id, user_id, labels, exc_info=True,
        )
        return 0


async def delete_meeting_xuanpu(
    meeting_id: str,
    user_id: str,
    username: str,
) -> bool:
    """调用 XuanPu MCP 软删除会议资产，并清理本地内存与临时文件"""
    # 1. 前置状态与权限校验（进行中状态严禁删除）
    active_m = active_meetings.get(meeting_id)

    has_local = False
    with SessionLocal() as db:
        local_rec = db.scalar(
            select(MeetingRecord).where(
                MeetingRecord.meeting_id == meeting_id,
                MeetingRecord.is_delete == False,  # noqa: E712
            )
        )
        if local_rec:
            if local_rec.user_id != user_id:
                raise errors.not_found("会议不存在或无权访问")
            if local_rec.status != "completed":
                raise errors.validation("仅已完成、已归档的会议纪要允许删除")
            delete_meeting(db, meeting_id, user_id)
            has_local = True
        elif active_m:
            if active_m.user_id != user_id:
                raise errors.not_found("会议不存在或无权访问")
            if active_m.status != "completed":
                raise errors.validation("仅已完成、已归档的会议纪要允许删除")

    active_meetings.remove(meeting_id)

    # 2. 调用 XuanPu MCP 软删除远端资产
    client = get_xuanpu_client()
    try:
        res = await client.call_tool(
            "xuanpu_meeting_delete",
            {"meeting_uid": meeting_id, "user_code": username},
            user_name=username,
        )
        if isinstance(res, str) and ("失败" in res or "Error" in res):
            if "仅已完成" in res or "HTTP 400" in res:
                raise errors.validation("仅已完成、已归档的会议纪要允许删除")
            if "不存在" in res or "HTTP 404" in res:
                if not has_local:
                    raise errors.not_found("会议不存在或无权访问")
    except errors.AppError:
        raise
    except Exception as exc:
        err_msg = str(exc)
        if "仅已完成" in err_msg or "HTTP 400" in err_msg:
            raise errors.validation("仅已完成、已归档的会议纪要允许删除")
        if "不存在" in err_msg or "HTTP 404" in err_msg:
            if not has_local:
                raise errors.not_found("会议不存在或无权访问")
            # 远端已不存在但本地有历史残留，允许幂等清理
        else:
            logger.warning("向 XuanPu 请求删除会议资产异常: %s", exc)
            raise errors.AppError("UPSTREAM_ERROR", STORAGE_NOT_READY_MSG, 503) from exc

    # 3. 清理本地临时文件目录
    shutil.rmtree(MEETING_LOCAL_DIR / meeting_id, ignore_errors=True)
    return True


async def regenerate_meeting_minutes(
    meeting_id: str,
    user_id: str,
    username: str,
) -> dict[str, Any]:
    """重新为已结束的会议生成纪要，适用于因大模型截断、超时或网络波动导致纪要为空后的重试补录。"""
    lock = hub.get_lock(f"regenerate_{meeting_id}")
    if lock.locked():
        raise errors.AppError(409, "该会议纪要正在重新生成中，请勿重复操作")

    async with lock:
        meeting_data = await get_meeting_xuanpu(meeting_id, user_id, username)
        status = meeting_data.get("status")
        if status not in ("completed", "error"):
            raise errors.AppError(400, f"当前会议处于 {status} 状态，仅已结束的会议支持重新生成纪要")

        # 任务防护治理：若该会议已存在已派发的待办任务，严禁重新生成以防任务数据脱节成为孤儿待办
        existing_action_items = meeting_data.get("action_items") or []
        has_dispatched = any(
            isinstance(item, dict) and (bool(item.get("task_id")) or item.get("status") == "dispatched")
            for item in existing_action_items
        )
        if has_dispatched:
            raise errors.AppError(400, "当前会议已有派发至待办中心的工作任务，为防止任务脱节，不支持重新生成纪要")

        transcript = (meeting_data.get("transcript_text") or "").strip()
        segments = meeting_data.get("segments") or []
        user_notes = meeting_data.get("user_notes") or []

        if not transcript and segments:
            transcript = "\n".join(
                f"[{float(s.get('start_time', 0.0)):.0f}s] {s.get('speaker_name') or '员工'}: {str(s.get('transcript', '')).strip()}"
                for s in segments
                if isinstance(s, dict) and s.get("transcript")
            ).strip()

        if not transcript and not user_notes:
            raise errors.AppError(400, "该会议暂无有效录音转写文本或笔记，无法重新生成纪要")

        title = meeting_data.get("title") or "会议研讨"
        duration_seconds = float(meeting_data.get("duration_seconds") or 0)
        started_at_raw = meeting_data.get("started_at")
        started_at = None
        if started_at_raw:
            try:
                started_at = datetime.fromisoformat(str(started_at_raw).replace("Z", "+00:00"))
            except Exception:
                started_at = datetime.now(SHANGHAI_TZ)
        else:
            started_at = datetime.now(SHANGHAI_TZ)

        # 重新生成纪要（调用带截断防御与低思考配置的统一生成入口）
        final_title, summary_text, sanitized_action_items, markdown_content = (
            await generate_minutes_from_transcript(
                title=title,
                transcript=transcript,
                user_notes=user_notes,
                total_duration=duration_seconds,
                started_at=started_at,
            )
        )

        # 覆写保存至 XuanPu 存储服务
        xuanpu = get_xuanpu_client()
        xuanpu_save_data = {
            "meeting_uid": meeting_id,
            "title": final_title,
            "status": "completed",
            "end_reason": meeting_data.get("end_reason") or "manual",
            "source": "xinhere",
            "user_code": username,
            "user_name": username,
            "started_at": started_at_raw if started_at_raw else started_at.isoformat(),
            "ended_at": meeting_data.get("ended_at"),
            "duration_seconds": int(round(duration_seconds)),
            "audio_file": "audio.wav",
            "audio_object_key": meeting_data.get("audio_storage_key") or "",
            "minutes_content": markdown_content,
            "minutes_storage_key": "",
            "summary": summary_text,
            "action_items": sanitized_action_items,
            "user_notes": user_notes,
            "analysis_result": meeting_data.get("analysis_result") or {},
            "transcript_text": transcript,
            "segments": segments,
            "speakers": meeting_data.get("speakers") or [],
        }

        try:
            save_res = await xuanpu.call_tool("xuanpu_meeting_save", xuanpu_save_data, user_name=username)
            if isinstance(save_res, str) and ("失败" in save_res or "Error" in save_res):
                logger.error("XuanPu 保存重生成纪要报错: %s", save_res)
                raise errors.AppError("UPSTREAM_ERROR", STORAGE_NOT_READY_MSG, 503)
        except errors.AppError:
            raise
        except Exception as save_err:
            logger.error("保存重生成纪要至 XuanPu 异常: %s", save_err)
            raise errors.AppError("UPSTREAM_ERROR", STORAGE_NOT_READY_MSG, 503) from save_err

        # 同步更新本地数据库临时记录（若存在）
        with SessionLocal() as db:
            local_rec = db.query(MeetingRecord).filter(
                MeetingRecord.meeting_id == meeting_id,
                MeetingRecord.user_id == user_id,
            ).first()
            if local_rec:
                local_rec.title = final_title
                local_rec.status = "completed"
                local_rec.summary = summary_text
                local_rec.minutes_content = markdown_content
                local_rec.action_items = sanitized_action_items
                db.commit()

        # SSE 广播刷新通知
        await hub.broadcast(meeting_id, "completed", {
            "status": "completed",
            "meeting_id": meeting_id,
            "title": final_title,
            "duration_seconds": int(round(duration_seconds)),
            "summary": summary_text,
            "action_items": sanitized_action_items,
            "minutes_content": markdown_content,
            "user_notes": user_notes,
            "audio_url": f"/meeting/{meeting_id}/audio",
        })

        return await get_meeting_xuanpu(meeting_id, user_id, username)



# ---- 会议超时看门狗 (Watchdog) ----
async def check_timeout_meetings() -> list[dict[str, str]]:
    """看门狗超时核验逻辑：
    检查内存及数据库中处于 recording 或 paused 状态的活跃会议：
    1. status == 'recording' 且距 last_active_at 超过 settings.meeting_recording_timeout_minutes 分钟：
       自动调度 finalize_meeting_task(meeting_id, user_id, reason="timeout_recording")
    2. status == 'paused' 且距 last_active_at 超过 settings.meeting_paused_timeout_minutes 分钟：
       自动调度 finalize_meeting_task(meeting_id, user_id, reason="timeout_paused")
    3. status == 'processing' 且距 ended_at/last_active_at 超过 settings.meeting_processing_timeout_minutes 分钟：
       分场景恢复（reason="processing_recovered"）：
       - 本地仍有分片 → 重派 finalize_meeting_task（转写与切片已持久化，重跑幂等：lock 防重入 + completed 短路）；
       - 本地已无分片目录 → XuanPu 已保存成功（save 先于本地清理）、仅收尾未完成（进程崩溃窗口）→ 只补收尾清理，
         严禁重派归档——空分片会合成 0 秒静音 WAV 覆盖已归档音频；
       - 目录存在但无分片（异常态）→ 查 XuanPu 有归档则补清理，否则置 error 交用户重试。
    """
    now = time.time()
    recording_timeout_sec = max(1, settings.meeting_recording_timeout_minutes) * 60
    paused_timeout_sec = max(1, settings.meeting_paused_timeout_minutes) * 60
    processing_timeout_sec = max(1, settings.meeting_processing_timeout_minutes) * 60

    expired: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    # 1. 扫描内存活跃会议
    for m in active_meetings.list_all():
        if m.status not in ("recording", "paused"):
            continue
        last_active = getattr(m, "last_active_at", None)
        if last_active is None:
            last_active_ts = now
        elif isinstance(last_active, datetime):
            last_active_ts = last_active.timestamp()
        else:
            try:
                last_active_ts = float(last_active)
            except (TypeError, ValueError):
                last_active_ts = now
        elapsed = now - last_active_ts
        if m.status == "recording" and elapsed > recording_timeout_sec:
            expired.append({"meeting_id": m.meeting_id, "user_id": m.user_id, "reason": "timeout_recording"})
            seen_ids.add(m.meeting_id)
        elif m.status == "paused" and elapsed > paused_timeout_sec:
            expired.append({"meeting_id": m.meeting_id, "user_id": m.user_id, "reason": "timeout_paused"})
            seen_ids.add(m.meeting_id)

    # 2. 扫描数据库中可能在服务重启后未加载进内存的 recording / paused 会议
    with SessionLocal() as db:
        now_dt = datetime.now(SHANGHAI_TZ)
        db_recs = (
            db.query(MeetingRecord)
            .filter(
                MeetingRecord.status.in_(["recording", "paused", "processing"]),
                MeetingRecord.is_delete == False,
            )
            .all()
        )
        for r in db_recs:
            if r.meeting_id in seen_ids:
                continue
            last_dt = r.last_active_at or r.started_at or r.created_at
            if not last_dt:
                last_dt = now_dt
            elif last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=SHANGHAI_TZ)
            else:
                last_dt = last_dt.astimezone(SHANGHAI_TZ)

            diff_sec = (now_dt - last_dt).total_seconds()
            if r.status == "recording" and diff_sec > recording_timeout_sec:
                expired.append({"meeting_id": r.meeting_id, "user_id": r.user_id, "reason": "timeout_recording"})
                seen_ids.add(r.meeting_id)
            elif r.status == "paused" and diff_sec > paused_timeout_sec:
                expired.append({"meeting_id": r.meeting_id, "user_id": r.user_id, "reason": "timeout_paused"})
                seen_ids.add(r.meeting_id)
            elif r.status == "processing":
                # processing 计时基准优先用 ended_at（进入归档时已写入），与活跃计时隔离；
                # 超时重跑归档是幂等的（finalize 开头 lock 防重入 + completed 短路），
                # 原协程若还活着（LLM 长推理/大音频上传）会被 lock 挡住不会重复归档
                base_dt = r.ended_at or last_dt
                if base_dt.tzinfo is None:
                    base_dt = base_dt.replace(tzinfo=SHANGHAI_TZ)
                else:
                    base_dt = base_dt.astimezone(SHANGHAI_TZ)
                if (now_dt - base_dt).total_seconds() > processing_timeout_sec:
                    seg_dir = MEETING_LOCAL_DIR / r.meeting_id / "segments"
                    has_wavs = seg_dir.exists() and any(seg_dir.glob("*.wav"))
                    if has_wavs:
                        # 本地分片仍在：归档从未完成，安全重派
                        expired.append({"meeting_id": r.meeting_id, "user_id": r.user_id, "reason": "processing_recovered"})
                        seen_ids.add(r.meeting_id)
                    elif not seg_dir.exists():
                        # 本地目录已删：XuanPu 保存成功在前、收尾崩溃在后，只补收尾，
                        # 严禁重派——空分片会合成 0 秒静音 WAV 覆盖已归档音频
                        _finalize_local_cleanup(r.meeting_id)
                        logger.warning(
                            "会议 %s 归档已持久化至 XuanPu 但收尾未完成，看门狗补清理完成",
                            r.meeting_id,
                        )
                    else:
                        # 目录存在但无分片（异常态）：以 XuanPu 为准
                        try:
                            await get_meeting_xuanpu(r.meeting_id, r.user_id, r.username)
                        except Exception:
                            with SessionLocal() as db2:
                                rec2 = db2.query(MeetingRecord).filter(
                                    MeetingRecord.meeting_id == r.meeting_id,
                                ).first()
                                if rec2:
                                    rec2.status = "error"
                                    db2.commit()
                            logger.error(
                                "会议 %s processing 恢复失败：本地无分片且 XuanPu 无归档，已置 error 待重试",
                                r.meeting_id,
                            )
                        else:
                            _finalize_local_cleanup(r.meeting_id)
                            logger.warning(
                                "会议 %s 本地分片目录异常但 XuanPu 已有归档，看门狗补清理完成",
                                r.meeting_id,
                            )

    for item in expired:
        m_id = item["meeting_id"]
        u_id = item["user_id"]
        reason = item["reason"]
        logger.warning(
            "会议 %s 触发看门狗自动归档 (reason=%s, user_id=%s)",
            m_id,
            reason,
            u_id,
        )
        safe_create_task(finalize_meeting_task(m_id, u_id, reason=reason))

    return expired


_watchdog_task: Optional[asyncio.Task] = None


async def _watchdog_loop() -> None:
    logger.info(
        "会议看门狗后台巡检协程启动 (recording_timeout=%dm, paused_timeout=%dm)",
        settings.meeting_recording_timeout_minutes,
        settings.meeting_paused_timeout_minutes,
    )
    while True:
        try:
            await check_timeout_meetings()
        except asyncio.CancelledError:
            logger.info("会议看门狗后台巡检协程取消退出")
            break
        except Exception as exc:
            logger.warning("会议看门狗巡检异常: %s", exc)
        try:
            await asyncio.sleep(15)  # 15 秒快速轮巡检测
        except asyncio.CancelledError:
            break


def start_watchdog() -> None:
    global _watchdog_task
    try:
        loop = asyncio.get_running_loop()
        if _watchdog_task is None or _watchdog_task.done():
            _watchdog_task = loop.create_task(_watchdog_loop())
    except RuntimeError:
        pass


def stop_watchdog() -> None:
    global _watchdog_task
    if _watchdog_task and not _watchdog_task.done():
        _watchdog_task.cancel()
        _watchdog_task = None


async def writeback_action_items(
    meeting_id: str,
    raw: dict[str, Any],
    action_items: list[dict[str, Any]],
    user_name: str,
    max_retries: int = 2,
) -> None:
    """原子性回写会议待办事项至本地内存/数据库及 XuanPu 远端 pro_meeting_records（最大重试2次）。"""
    # 1. 更新本地活动态（若存在）
    act = active_meetings.get(meeting_id)
    if act:
        act.action_items = action_items

    # 2. 更新本地物理库临时记录（若存在）
    with SessionLocal() as db:
        local_rec = db.query(MeetingRecord).filter(MeetingRecord.meeting_id == meeting_id).first()
        if local_rec:
            local_rec.action_items = action_items
            db.commit()

    # 3. 回写 XuanPu 远端资产（归档单一事实源）
    client = get_xuanpu_client()
    xuanpu_user_id = None
    raw_uid = raw.get("user_id")
    if isinstance(raw_uid, int):
        xuanpu_user_id = raw_uid
    elif isinstance(raw_uid, str) and raw_uid.isdigit():
        xuanpu_user_id = int(raw_uid)

    payload = {
        "meeting_uid": raw.get("meeting_uid") or raw.get("meeting_id") or meeting_id,
        "title": raw.get("title", "会议纪要"),
        "status": raw.get("status", "completed"),
        "source": raw.get("source", "xinhere"),
        "user_code": raw.get("user_code") or user_name,
        "user_name": raw.get("user_name") or user_name,
        "user_id": xuanpu_user_id,
        "project_id": raw.get("project_id"),
        "started_at": raw.get("started_at") if raw.get("started_at") else None,
        "ended_at": raw.get("ended_at") if raw.get("ended_at") else None,
        "duration_seconds": raw.get("duration_seconds", 0),
        "audio_file": raw.get("audio_file", ""),
        "audio_object_key": raw.get("audio_object_key", ""),
        "minutes_content": raw.get("minutes_content", ""),
        "minutes_storage_key": raw.get("minutes_storage_key", ""),
        "summary": raw.get("summary", ""),
        "action_items": action_items,
        "user_notes": raw.get("user_notes") or [],
        "analysis_result": raw.get("analysis_result") or {},
        "transcript_text": raw.get("transcript_text", ""),
        "segments": raw.get("segments") or [],
        "speakers": raw.get("speakers") or [],
    }

    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            res = await client.call_tool("xuanpu_meeting_save", payload, user_name=user_name)
            if isinstance(res, str) and ("失败" in res or "Error" in res):
                raise RuntimeError(f"XuanPu 保存会议报错: {res}")
            logger.info("回写 XuanPu action_items 成功 (meeting_id=%s, attempt=%d)", meeting_id, attempt)
            return
        except Exception as exc:
            last_exc = exc
            logger.warning("回写 XuanPu action_items 失败 (attempt %d/%d): %s", attempt + 1, max_retries + 1, exc)
            if attempt < max_retries:
                await asyncio.sleep(0.5 * (2 ** attempt))

    logger.error("回写 XuanPu action_items 最终失败 (重试 %d 次): %s", max_retries, last_exc)
    raise errors.AppError("UPSTREAM_ERROR", f"回写 XuanPu 失败: {last_exc}", 503)


async def dispatch_meeting_action(
    db: Session,
    meeting_id: str,
    item_index: int,
    data: DispatchActionIn,
    user: SysUser,
) -> dict[str, Any]:
    """单条派发：复用 todo_svc 创建通用任务与处理人待办，并原子性回写 XuanPu 会议纪要。"""
    lock = hub.get_lock(f"dispatch_{meeting_id}")
    async with lock:
        meeting_data = await get_meeting_xuanpu(meeting_id, user.user_id, user.username)
        action_items = [dict(it) for it in (meeting_data.get("action_items") or [])]
        if item_index < 0 or item_index >= len(action_items):
            raise errors.validation(f"事项序号 {item_index} 超出范围")

        item = action_items[item_index]
        # 防重复提交：创建待办前先查该 action_item 是否已有 task_id
        if item.get("task_id") or item.get("status") == "dispatched":
            raise errors.validation("该待办事项已派发，不可重复派发")

        title = (data.title or item.get("title", "")).strip()
        if not title:
            raise errors.validation("待办标题不能为空")

        assignee_id = data.assignee_id.strip()
        if not assignee_id:
            raise errors.validation("请选择承办人")

        assignee_name = (data.assignee_name or item.get("assignee", "")).strip()
        note = data.note.strip() if data.note else ""

        due_dt = None
        if data.due and data.due not in ("未明确", "待定"):
            try:
                due_dt = datetime.fromisoformat(data.due)
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=SHANGHAI_TZ)
            except Exception:
                pass

        meeting_title = meeting_data.get("title") or "会议纪要"
        try:
            task = todo_svc.create_task(
                db,
                scene="generic",
                title=title,
                dispatcher_id=user.user_id,
                payload={"content": note, "source": "meeting", "meeting_id": meeting_id},
                review_required=True,
                due=due_dt,
            )
            todo_svc.create_todo(
                db,
                task=task,
                assignee_id=assignee_id,
                kind="action",
                title=title,
                sub=note,
                ref={"source": "meeting", "meeting_id": meeting_id, "title": meeting_title},
                due=due_dt,
            )

            # 更新条目状态与 task_id
            item["status"] = "dispatched"
            item["task_id"] = task.task_id
            item["assignee_id"] = assignee_id
            item["assignee_name"] = assignee_name
            item["dispatched_at"] = datetime.now(SHANGHAI_TZ).isoformat()
            if data.due:
                item["due"] = data.due
            action_items[item_index] = item

            # 原子性回写 XuanPu：回写成功后提交事务，失败则回滚杜绝幽灵待办
            await writeback_action_items(meeting_id, meeting_data, action_items, user.username)

            # 同步更新本地数据库临时记录与内存态（若存在）
            local_rec = db.query(MeetingRecord).filter(
                MeetingRecord.meeting_id == meeting_id,
                MeetingRecord.user_id == user.user_id,
            ).first()
            if local_rec:
                local_rec.action_items = action_items
            active_m = active_meetings.get(meeting_id)
            if active_m:
                active_m.action_items = action_items

            db.commit()
            return {"task_id": task.task_id, "action_item": item}
        except Exception as exc:
            db.rollback()
            if isinstance(exc, errors.AppError):
                raise
            logger.exception("单条派发失败 (meeting_id=%s, item=%d): %s", meeting_id, item_index, exc)
            raise errors.AppError("UPSTREAM_ERROR", f"派发待办失败: {exc}", 503) from exc


async def batch_dispatch_meeting_actions(
    db: Session,
    meeting_id: str,
    data: BatchDispatchIn,
    user: SysUser,
) -> dict[str, Any]:
    """批量派发：逐项派发、逐行反馈结果与错误信息，失败项不影响已成功项。"""
    lock = hub.get_lock(f"dispatch_{meeting_id}")
    async with lock:
        meeting_data = await get_meeting_xuanpu(meeting_id, user.user_id, user.username)
        action_items = [dict(it) for it in (meeting_data.get("action_items") or [])]
        meeting_title = meeting_data.get("title") or "会议纪要"

        results: list[dict[str, Any]] = []
        created_items: list[tuple[int, dict[str, Any]]] = []

        for entry in data.items:
            idx = entry.item_index
            if idx < 0 or idx >= len(action_items):
                results.append({"item_index": idx, "ok": False, "error": f"事项序号 {idx} 超出范围"})
                continue

            item = action_items[idx]
            if item.get("task_id") or item.get("status") == "dispatched":
                results.append({"item_index": idx, "ok": False, "error": "该事项已派发，不可重复派发"})
                continue

            title = (entry.title or item.get("title", "")).strip()
            if not title:
                results.append({"item_index": idx, "ok": False, "error": "待办标题不能为空"})
                continue

            assignee_id = entry.assignee_id.strip()
            if not assignee_id:
                results.append({"item_index": idx, "ok": False, "error": "请指定承办人"})
                continue

            assignee_name = (entry.assignee_name or item.get("assignee", "")).strip()
            note = entry.note.strip() if entry.note else ""

            due_dt = None
            if entry.due and entry.due not in ("未明确", "待定"):
                try:
                    due_dt = datetime.fromisoformat(entry.due)
                    if due_dt.tzinfo is None:
                        due_dt = due_dt.replace(tzinfo=SHANGHAI_TZ)
                except Exception:
                    pass

            try:
                task = todo_svc.create_task(
                    db,
                    scene="generic",
                    title=title,
                    dispatcher_id=user.user_id,
                    payload={"content": note, "source": "meeting", "meeting_id": meeting_id},
                    review_required=True,
                    due=due_dt,
                )
                todo_svc.create_todo(
                    db,
                    task=task,
                    assignee_id=assignee_id,
                    kind="action",
                    title=title,
                    sub=note,
                    ref={"source": "meeting", "meeting_id": meeting_id, "title": meeting_title},
                    due=due_dt,
                )

                new_item = dict(item)
                new_item["status"] = "dispatched"
                new_item["task_id"] = task.task_id
                new_item["assignee_id"] = assignee_id
                new_item["assignee_name"] = assignee_name
                new_item["dispatched_at"] = datetime.now(SHANGHAI_TZ).isoformat()
                if entry.due:
                    new_item["due"] = entry.due
                action_items[idx] = new_item
                created_items.append((idx, new_item))
            except Exception as exc:
                logger.warning("批量派发第 %d 项创建任务失败: %s", idx, exc)
                results.append({"item_index": idx, "ok": False, "error": str(exc)})

        if created_items:
            try:
                await writeback_action_items(meeting_id, meeting_data, action_items, user.username)
                local_rec = db.query(MeetingRecord).filter(
                    MeetingRecord.meeting_id == meeting_id,
                    MeetingRecord.user_id == user.user_id,
                ).first()
                if local_rec:
                    local_rec.action_items = action_items
                active_m = active_meetings.get(meeting_id)
                if active_m:
                    active_m.action_items = action_items

                db.commit()
                for idx, itm in created_items:
                    results.append({"item_index": idx, "ok": True, "task_id": itm["task_id"], "action_item": itm})
            except Exception as exc:
                db.rollback()
                logger.error("批量派发回写 XuanPu 失败，已执行本地事务回滚: %s", exc)
                for idx, _ in created_items:
                    results.append({"item_index": idx, "ok": False, "error": f"回写会议纪要失败: {exc}"})

        results.sort(key=lambda x: x["item_index"])
        return {"results": results}



