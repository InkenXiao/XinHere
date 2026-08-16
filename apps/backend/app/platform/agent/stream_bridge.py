from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue


class StreamBridge:
    """DB 写线程 → SSE 消费者的桥。断线只退订，run 不终止。"""

    def __init__(self):
        self._subs: dict[str, list[_Subscriber]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2048)
        sub = _Subscriber(loop=asyncio.get_running_loop(), queue=q)
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
                sub.loop.call_soon_threadsafe(sub.queue.put_nowait, frame)
            except (RuntimeError, asyncio.QueueFull):
                pass

    def publish_marker(self, session_id: str, status: str) -> None:
        """run 生命周期标记（不落库、不下发）。"""
        self.publish(session_id, {"type": "_marker", "id": "", "data": {"status": status}})


bridge = StreamBridge()
