from __future__ import annotations

import contextvars
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol

from langgraph.errors import GraphInterrupt

from ...core.config import settings
from ...core.context import AuditCtx, reset_ctx, set_ctx
from ...persistence.session import SessionLocal
from ..scms.token_auth import bind_user_token, reset_user_token
from . import tracing

logger = logging.getLogger(__name__)


class ToolAnswerSink(Protocol):
    """工具答案直通正文通道（EventTranslator 实现）：begin 开启正文段、
    emit 输出片段、end 固化收尾。"""

    def begin_tool_answer(self) -> None: ...
    def emit_tool_answer(self, piece: str) -> None: ...
    def end_tool_answer(self) -> None: ...


class ToolAnswerStream:
    """工具答案直通正文的统一生命周期封装（问数流 / 知识库直连问答共用）。

    - begin：打开通道（幂等）；首次打开且指定了 tool_name 时补发
      streaming_partial 进度标记
    - append：追加正文片段，未开通时自动开通
    - end：固化本段直通正文；通道从未开通（无正文）时为空操作
    - streamed：是否已发生直通输出（供工具返回话术判断）

    sink 为 None（无直通通道）时全部为空操作。
    """

    def __init__(self, sink: ToolAnswerSink | None, tool_name: str | None = None,
                 progress: tuple[str, str] = ("streaming_partial", "答案生成中…")):
        self._sink = sink
        self._tool_name = tool_name
        self._progress = progress
        self.streamed = False

    def begin(self) -> None:
        if self._sink is None or self.streamed:
            return
        self._sink.begin_tool_answer()
        self.streamed = True
        if self._tool_name and self._progress and hasattr(self._sink, "emit_tool_progress"):
            self._sink.emit_tool_progress(None, self._tool_name, *self._progress)

    def append(self, piece: str) -> None:
        if self._sink is None or not piece:
            return
        self.begin()
        self._sink.emit_tool_answer(piece)
        # 同步累积到当前工具调用的交付物收集器（供 tool_scope 收尾时落 Langfuse）
        collector = _current_collector.get()
        if collector is not None:
            collector.add_answer(piece)

    def end(self) -> None:
        if self._sink is not None and self.streamed:
            self._sink.end_tool_answer()


class _DeliverableCollector:
    """单次工具调用的交付物收集器：汇总卡片事件与直通答案正文。

    由 tool_scope 在工具调用开始时创建并绑定到当前线程上下文，
    工具内所有经 ctx.emit_event / ToolAnswerStream.append 的交付物自动累积，
    工具调用结束时统一一次性补录到 Langfuse，业务工具无需感知追踪。"""

    def __init__(self):
        self.events: list[dict] = []   # 卡片/交付物事件 {"type", "payload"}
        self.answer: list[str] = []    # 直通答案正文片段

    def add_event(self, type_: str, payload: dict) -> None:
        self.events.append({"type": type_, "payload": payload})

    def add_answer(self, piece: str) -> None:
        self.answer.append(piece)

    def take(self) -> dict | None:
        """取出已收集交付物并清空；无交付物返回 None。"""
        events = self.events
        answer = "".join(self.answer)
        self.events = []
        self.answer = []
        if not events and not answer:
            return None
        return {"events": events, "answer": answer}


# 当前工具调用（tool_scope 域内）的交付物收集器；无活动工具调用时为 None
_current_collector: contextvars.ContextVar[_DeliverableCollector | None] = contextvars.ContextVar(
    "tool_deliverable_collector", default=None)


@dataclass
class ToolCtx:
    """工具上下文：数据权限与审计溯源绑定（07 §6）。"""

    user_id: str
    session_id: str
    request_id: str
    turn: int = 0
    emit: Callable[[str, dict], tuple[int, datetime]] | None = None  # store.append
    scms_token: str = ""  # 发起用户的 SCMS token（业务调用透传；不依赖框架线程上下文传播）
    raw_input: str = ""  # 本轮用户原始输入（query_data 等工具强制用它，不信模型转述）
    enable_web_search: bool = False  # 本轮联网搜索开关（前端随 chat 请求传入，query_data 透传问数服务）
    space_ids: list[int] | None = None  # 本轮知识库库 id 数组（query_data 以 spaceIds 原样透传问数上游）
    file_ids: list[int] | None = None  # 本轮知识库范围：文件 id（query_data 以 fileIds 原样透传问数上游）
    folder_ids: list[int] | None = None  # 本轮知识库范围：文件夹 id（query_data 以 folderIds 原样透传问数上游）
    answer_stream: ToolAnswerSink | None = None  # EventTranslator：工具直通正文实时流（begin/emit/end_tool_answer）
    extra: dict = field(default_factory=dict)

    def emit_event(self, type_: str, payload: dict) -> tuple[int, datetime] | None:
        if self.emit is None:
            return None
        # 累积到当前工具调用的交付物收集器（tool_scope 收尾时统一补录 Langfuse，
        # trace 里可见真实交付的 JSON——CallbackHandler 只能捕获返回值占位符）
        collector = _current_collector.get()
        if collector is not None:
            collector.add_event(type_, payload)
        return self.emit(type_, payload)


@contextmanager
def tool_scope(ctx: ToolCtx, name: str, call_id: str, arguments: dict):
    """工具执行域：注入审计上下文（channel=tool，actor=模型+工具名），提供落库 session。

    detail.context 携带 {call_id, tool_call_arguments, operator_user_id}（AI 溯源）。
    SCMS 用户 token 在此显式绑定——工具可能跑在 ToolNode 线程池，contextvars
    经多层框架（langchain executor / deepagents middleware）传播不保证，带在 ctx 上才可靠。
    """
    audit = AuditCtx(
        user_id=ctx.user_id,
        channel="tool",
        actor=f"{settings.main_model}+{name}",
        session_id=ctx.session_id or None,
        request_id=ctx.request_id,
        entry_point=name,
        detail_context={
            "context": {
                "call_id": call_id or "",
                "tool_call_arguments": arguments,
                "operator_user_id": ctx.user_id,
            }
        },
    )
    token = set_ctx(audit)
    utk = bind_user_token(ctx.scms_token) if ctx.scms_token else None
    # 交付物收集器：本次工具调用经 emit_event / 直通流输出的全部内容在此累积，
    # 收尾时一次性补录 Langfuse（详见 tracing.flush_tool_deliverables）
    collector = _DeliverableCollector()
    collector_token = _current_collector.set(collector)
    try:
        with SessionLocal() as db:
            try:
                yield db
                db.commit()
                logger.info("tool_scope 提交 sid=%s tool=%s call_id=%s user=%s",
                            ctx.session_id, name, call_id, ctx.user_id)
            except GraphInterrupt:
                # interrupt 挂起是正常控制流：未提交写随会话关闭丢弃，不记回滚告警
                logger.info("tool_scope 挂起（interrupt） sid=%s tool=%s call_id=%s",
                            ctx.session_id, name, call_id)
                raise
            except Exception as exc:
                logger.warning("tool_scope 回滚 sid=%s tool=%s call_id=%s err=%r",
                               ctx.session_id, name, call_id, exc)
                raise
    finally:
        # 先取走交付物并解绑收集器，再补录——补录 fail-soft 且与审计/token 解绑顺序无关
        deliverables = collector.take()
        _current_collector.reset(collector_token)
        if deliverables is not None:
            tracing.flush_tool_deliverables(name=name, arguments=arguments,
                                            deliverables=deliverables)
        reset_ctx(token)
        if utk is not None:
            reset_user_token(utk)


def new_component_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex[:12]}"
