from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class _Subscriber:
    session_id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue
    overflows: int = 0


class StreamBridge:
    """DB 写线程 → SSE 消费者的桥。断线只退订，run 不终止。

    队列满（消费端停摆：网络背压/断开未感知）时丢最旧保最新——按序事件流中
    终止帧（turn/end、_marker done）必须可达，否则消费端恢复后流永远收不了尾；
    丢失的中间 chunk 由前端断线重连（Last-Event-ID → after_seq 回放）补齐。
    连续溢出超过阈值判死订阅退订，防无消费者订阅永久泄漏。"""

    _DEAD_AFTER_OVERFLOWS = 64

    def __init__(self):
        self._subs: dict[str, list[_Subscriber]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2048)
        sub = _Subscriber(session_id=session_id, loop=asyncio.get_running_loop(), queue=q)
        with self._lock:
            self._subs[session_id].append(sub)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs[session_id] = [s for s in self._subs.get(session_id, []) if s.queue is not q]

    def publish(self, session_id: str, frame: dict) -> None:
        """frame: {type, id, data}；可从任意线程调用。"""
        with self._lock:
            subs = list(self._subs.get(session_id, []))
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(self._put, sub, frame)
            except RuntimeError:
                pass  # 事件循环已关闭（服务停机窗口）：直接丢弃

    def _put(self, sub: _Subscriber, frame: dict) -> None:
        """事件循环线程内入队：满则丢最旧保最新；长期无消费判死退订。"""
        try:
            sub.queue.put_nowait(frame)
            sub.overflows = 0
            return
        except asyncio.QueueFull:
            sub.overflows += 1
            if sub.overflows > self._DEAD_AFTER_OVERFLOWS:
                self.unsubscribe(sub.session_id, sub.queue)
                return
            try:
                sub.queue.get_nowait()  # 腾位：丢最旧（中间 chunk 可由重连回放补齐）
            except asyncio.QueueEmpty:
                pass
            try:
                sub.queue.put_nowait(frame)
            except asyncio.QueueFull:
                pass

    def publish_marker(self, session_id: str, status: str) -> None:
        """run 生命周期标记（不落库、不下发）。"""
        self.publish(session_id, {"type": "_marker", "id": "", "data": {"status": status}})


bridge = StreamBridge()
