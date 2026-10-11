"""会议纪要模块数据模型：主表 meeting_records 与分片表 meeting_segments。"""
from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB

from ...persistence.base import BusinessBase


def _meeting_id() -> str:
    return f"mtg_{uuid.uuid4().hex}"


def _segment_id() -> str:
    return f"seg_{uuid.uuid4().hex}"


class MeetingRecord(BusinessBase):
    """会议纪要主表：记录会议全生命周期、MinIO 存储归档及 AI 分析成果。"""

    __tablename__ = "meeting_records"

    meeting_id = Column(
        String(64), primary_key=True, default=_meeting_id,
        comment="会议 ID（前缀 mtg_ + uuid hex）",
    )
    user_id = Column(String(64), nullable=False, comment="发起人用户 ID")
    username = Column(String(64), nullable=False, comment="发起人用户名（用于 MinIO 目录归档）")
    title = Column(String(255), nullable=False, default="未命名会议", comment="会议主题")
    status = Column(
        String(32), nullable=False, default="recording",
        comment="状态：recording=录音中 / paused=已暂停 / processing=整理生成中 / completed=已完成 / error=异常",
    )
    started_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="开始时间",
    )
    ended_at = Column(DateTime(timezone=True), nullable=True, comment="结束时间")
    end_reason = Column(
        String(64), nullable=True,
        comment="结束原因：manual=手动结束 / timeout_recording=录音中断超时 / timeout_paused=暂停超时",
    )
    last_active_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后活跃时间（心跳/切片上传/状态操作）",
    )
    duration_seconds = Column(
        Integer, nullable=False, default=0,
        comment="累计录音时长（秒，上限 10800 秒 / 3 小时）",
    )
    audio_storage_key = Column(String(512), nullable=True, comment="完整音频 MinIO 存储路径")
    minutes_storage_key = Column(String(512), nullable=True, comment="Markdown 纪要 MinIO 存储路径")
    summary = Column(Text, nullable=True, comment="会后纪要核心摘要")
    minutes_content = Column(Text, nullable=True, comment="完整 Markdown 结构化会议纪要")
    analysis_result = Column(
        JSONB, nullable=False, default=dict, server_default="{}",
        comment="AI 战投 6 维动态研判与实时洞察",
    )
    action_items = Column(
        JSONB, nullable=False, default=list, server_default="[]",
        comment="会后行动项列表（保留一键转待办功能）",
    )
    transcript_text = Column(
        Text, nullable=False, default="", server_default="",
        comment="累积全文字幕文本",
    )
    user_notes = Column(
        JSONB, nullable=False, default=list, server_default="[]",
        comment="会议关键信息补充列表 [{id, time_sec, content, created_at}]",
    )
    speakers = Column(
        JSONB, nullable=False, default=list, server_default="[]",
        comment="发言人列表（预留）",
    )

    __table_args__ = (
        Index("ix_meeting_records_user", "user_id", "created_at"),
        {"comment": "会议纪要主表：记录会议生命周期、存储路径及 AI 分析成果"},
    )


class MeetingSegment(BusinessBase):
    """会议音频分片与实时转写表：记录 5 秒音频切片元数据及 ASR 转写结果。"""

    __tablename__ = "meeting_segments"

    segment_id = Column(
        String(64), primary_key=True, default=_segment_id,
        comment="分片 ID（前缀 seg_ + uuid hex）",
    )
    meeting_id = Column(String(64), nullable=False, index=True, comment="所属会议 ID")
    seq_num = Column(Integer, nullable=False, comment="切片序号（从 0 开始自增）")
    start_time = Column(Float, nullable=False, default=0.0, comment="切片起始时间相对秒数")
    end_time = Column(Float, nullable=False, default=0.0, comment="切片结束时间相对秒数")
    duration = Column(Float, nullable=False, default=5.0, comment="切片音频时长秒数")
    transcript = Column(Text, nullable=False, default="", server_default="", comment="ASR 清洗后文本")
    raw_transcript = Column(Text, nullable=False, default="", server_default="", comment="ASR 原始返回文本")
    audio_path = Column(String(512), nullable=True, comment="本地切片文件绝对/相对路径")
    speaker_id = Column(String(32), nullable=True, comment="发言人 ID（预留）")
    speaker_name = Column(
        String(64), nullable=False, default="员工", server_default="员工",
        comment="发言人名称（本轮固定为'员工'）",
    )

    __table_args__ = (
        Index("ix_meeting_segments_mtg_seq", "meeting_id", "seq_num"),
        {"comment": "会议音频分片与转写：记录 5 秒音频切片与 ASR 结果"},
    )
