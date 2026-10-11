"""todo/changed 用户事件总线（P5 触达）：内核每次流转 publish，/todos/stream SSE 订阅。

进程内 pub/sub（单实例部署够用）；每用户一个小队列（满则丢最旧，触达语义允许）。
"""

from __future__ import annotations

import logging
import queue
import threading

logger = logging.getLogger(__name__)

_subs: dict[str, list[queue.Queue]] = {}
_lock = threading.Lock()

_QUEUE_MAX = 64


def subscribe(user_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
    with _lock:
        _subs.setdefault(user_id, []).append(q)
    return q


def unsubscribe(user_id: str, q: queue.Queue) -> None:
    with _lock:
        lst = _subs.get(user_id) or []
        if q in lst:
            lst.remove(q)
        if not lst:
            _subs.pop(user_id, None)


def publish(user_ids: list[str] | set[str], type_: str, payload: dict) -> None:
    """向一组用户推送事件帧 {type, data}（队列满丢最旧：实时触达允许丢帧，轮询兜底）。"""
    frame = {"type": type_, "data": payload}
    with _lock:
        for uid in set(user_ids):
            if not uid:
                continue
            for q in _subs.get(uid, []):
                try:
                    q.put_nowait(frame)
                except queue.Full:  # 丢最旧保新
                    try:
                        q.get_nowait()
                        q.put_nowait(frame)
                    except (queue.Empty, queue.Full):
                        pass


def online_user_ids() -> list[str]:
    with _lock:
        return list(_subs.keys())
