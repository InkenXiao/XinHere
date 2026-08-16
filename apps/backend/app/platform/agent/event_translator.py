from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

from ..events.store import EventStore


class EventTranslator:
    """LangGraph stream 事件 → 平台事件帧。配对前置：写 assistant/message 前先 flush chunk 队列。"""

    def __init__(self, store: EventStore, session_id: str, turn: int):
        self.store = store
        self.session_id = session_id
        self.turn = turn
        self.step = 0
        self.step_open = False
        self.content = ""
        self.usage: dict | None = None
        self.call_seqs: dict[str, int] = {}

    # ---- messages 模式：LLM chunk ----
    def handle_chunk(self, chunk, meta: dict) -> None:
        if not isinstance(chunk, AIMessage):
            return
        if getattr(chunk, "tool_call_chunks", None):
            return  # 工具调用装配块不进正文
        text = chunk.content
        if isinstance(text, list):
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        if not text:
            um = getattr(chunk, "usage_metadata", None)
            if um:
                self.usage = {"prompt": um.get("input_tokens", 0), "completion": um.get("output_tokens", 0)}
            return
        if not self.step_open:
            self.step += 1
            self.step_open = True
            self.store.append(
                self.session_id, "step/start", {"turn": self.turn, "step": self.step, "version": 1},
                turn=self.turn,
            )
        self.content += text
        self.store.append_chunk(
            self.session_id,
            {"turn": self.turn, "step": self.step, "delta": text, "version": 1},
            turn=self.turn,
        )
        um = getattr(chunk, "usage_metadata", None)
        if um:
            self.usage = {"prompt": um.get("input_tokens", 0), "completion": um.get("output_tokens", 0)}

    # ---- updates 模式：节点完成 ----
    def handle_update(self, update: dict) -> None:
        for _node, payload in (update or {}).items():
            if not isinstance(payload, dict):
                continue
            messages = payload.get("messages") or []
            if not isinstance(messages, list):
                messages = [messages]
            for msg in messages:
                if isinstance(msg, AIMessage):
                    self._finalize_step()
                    for tc in msg.tool_calls or []:
                        call_id = tc.get("id") or ""
                        seq, _ = self.store.append(
                            self.session_id,
                            "tool/call",
                            {"call_id": call_id, "name": tc.get("name", ""),
                             "arguments": tc.get("args") or {}, "version": 1},
                            turn=self.turn,
                        )
                        self.call_seqs[call_id] = seq
                elif isinstance(msg, ToolMessage):
                    self._finalize_step()
                    content = msg.content
                    if isinstance(content, list):
                        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                    call_id = msg.tool_call_id or ""
                    refs = [self.call_seqs[call_id]] if call_id in self.call_seqs else []
                    self.store.append(
                        self.session_id,
                        "tool/result",
                        {"call_id": call_id, "name": msg.name or "", "content": str(content)[:4000],
                         "is_error": msg.status == "error", "refs": refs, "version": 1},
                        turn=self.turn,
                    )

    def _finalize_step(self) -> None:
        """固化当前 step：取 flusher 累积区 + 队列残余的全部 chunk seq 配对，再写 assistant/message。"""
        if not self.step_open:
            return
        chunk_seqs = self.store.drained_chunk_seqs(self.session_id)
        if self.content:
            self.store.append(
                self.session_id,
                "assistant/message",
                {"turn": self.turn, "step": self.step, "content": self.content,
                 "usage": self.usage, "source_event_seqs": chunk_seqs, "version": 1},
                turn=self.turn,
            )
        self.store.append(
            self.session_id, "step/end", {"turn": self.turn, "step": self.step, "version": 1},
            turn=self.turn,
        )
        self.step_open = False
        self.content = ""
        self.usage = None

    def finalize(self) -> None:
        self._finalize_step()
        self.store.flush_chunks(self.session_id)
        self.store.clear_chunk_state(self.session_id)  # turn 边界清理，防累积区滞留
