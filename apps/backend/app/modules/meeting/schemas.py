"""会议纪要模块 Pydantic 模型契约。"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class CreateMeetingIn(BaseModel):
    title: str = Field("", description="会议主题，留空自动生成")


class UpdateMeetingIn(BaseModel):
    title: str | None = Field(None, description="更新会议主题")


class MeetingNoteIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000, description="笔记内容")
    time_sec: float = Field(0.0, ge=0.0, description="记录时相对会议开始的秒数")


class MeetingNoteOut(BaseModel):
    id: str
    time_sec: float
    content: str
    created_at: str


class MeetingActionItemSchema(BaseModel):
    title: str
    assignee: str = ""
    assignee_id: str | None = None
    assignee_name: str | None = None
    task_id: str | None = None
    dispatched_at: str | None = None
    due: str = ""
    status: str = "pending"
    priority: str = "medium"


class MeetingInsightDimensionSchema(BaseModel):
    name: str
    score: float = 80.0
    insights: str = ""
    sentiment: str = "positive"


class MeetingAnalysisResultSchema(BaseModel):
    current_topic: str = ""
    core_takeaways: list[str] = Field(default_factory=list)
    issues_and_risks: list[str] = Field(default_factory=list)
    potential_solutions: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    # 向后兼容
    overall: str = ""
    dimensions: list[MeetingInsightDimensionSchema] = Field(default_factory=list)
    action_items: list[MeetingActionItemSchema] = Field(default_factory=list)
    key_topics: list[str] = Field(default_factory=list)


class MeetingSegmentOut(BaseModel):
    segment_id: str
    meeting_id: str
    seq_num: int
    start_time: float
    end_time: float
    duration: float
    transcript: str
    speaker_id: str | None = None
    speaker_name: str = "员工"


class MeetingDetailOut(BaseModel):
    meeting_id: str
    user_id: str
    username: str
    title: str
    status: str
    started_at: str
    ended_at: str | None = None
    end_reason: str | None = None
    last_active_at: str | None = None
    duration_seconds: int
    audio_storage_key: str | None = None
    minutes_storage_key: str | None = None
    summary: str | None = None
    minutes_content: str | None = None
    analysis_result: dict[str, Any] = Field(default_factory=dict)
    action_items: list[dict[str, Any]] = Field(default_factory=list)
    transcript_text: str = ""
    user_notes: list[MeetingNoteOut] = Field(default_factory=list)
    segments: list[MeetingSegmentOut] = Field(default_factory=list)
    speakers: list[dict[str, Any]] = Field(default_factory=list)


class SpeakerRenameIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=32, description="原说话人编号标签（如：说话人1）")
    name: str | None = Field(None, max_length=32, description="自定义显示名（自由文本改名）")
    resolved_user_id: str | None = Field(None, description="绑定的实名用户 ID（优先于 name）")


class DispatchActionIn(BaseModel):
    title: str | None = Field(None, description="待办标题（留空沿用会议事项标题）")
    assignee_id: str = Field(..., description="承办人用户ID")
    assignee_name: str = Field("", description="承办人姓名")
    due: str | None = Field(None, description="截止时间 YYYY-MM-DD 或 ISO 格式")
    note: str = Field("", description="待办补充说明")


class BatchDispatchItemIn(BaseModel):
    item_index: int = Field(..., ge=0, description="事项索引（从 0 开始）")
    title: str | None = Field(None, description="待办标题")
    assignee_id: str = Field(..., description="承办人用户ID")
    assignee_name: str = Field("", description="承办人姓名")
    due: str | None = Field(None, description="截止时间")
    note: str = Field("", description="待办补充说明")


class BatchDispatchIn(BaseModel):
    items: list[BatchDispatchItemIn] = Field(..., min_length=1, description="待派发事项列表")


class MeetingHistoryItem(BaseModel):
    meeting_id: str
    title: str
    status: str
    started_at: str
    duration_seconds: int
    summary: str | None = None
    action_item_count: int = 0
    notes_count: int = 0
    end_reason: str | None = None


class MeetingHistoryOut(BaseModel):
    items: list[MeetingHistoryItem]
    total: int
    page: int
    page_size: int

