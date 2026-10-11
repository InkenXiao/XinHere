"""逆回购行情提醒接入（上游 scms-backend 公共 WS 流）。

后端单连接上游 ws://…/treasury/v1/repo-alerts/stream，收到 treasury-repo-alert 后
经 todo_bus 扇出给全部在线用户（repo/alerted）；同时维护连接状态 + 环形日志，
供 /repo-alerts/status|/log 页面展示（没有提醒时靠心跳日志体现链路活着）。
参照 touch.py 的 daemon 线程模式；REPO_ALERT_WS_URL 未配置=功能整体不启动。
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from datetime import datetime, timezone

from ..core.config import settings
from ..platform.events import todo_bus

logger = logging.getLogger(__name__)

_LOG_MAX = 1000  # 环形日志上限：30s 心跳下约覆盖 8 小时，够排查一个交易日

_lock = threading.Lock()
_status: dict = {
    "enabled": False,
    "state": "stopped",  # stopped | connecting | online | disconnected
    "url": "",
    "connected_at": None,
    "last_heartbeat_at": None,
    "last_alert_at": None,
    "last_error": None,
    "alert_count": 0,
    "heartbeat_count": 0,
    "reconnect_count": 0,
}
_log: deque[dict] = deque(maxlen=_LOG_MAX)
_started = False


def _log_entry(kind: str, message: str, data: dict | None = None) -> None:
    entry = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind,
             "message": message, "data": data}
    with _lock:
        _log.append(entry)


def _set(**kw) -> None:
    with _lock:
        _status.update(kw)


def status() -> dict:
    with _lock:
        return dict(_status)


def log_items(limit: int = 200) -> list[dict]:
    with _lock:
        items = list(_log)
    return items[-limit:]


def _handle_message(raw: str) -> None:
    try:
        frame = json.loads(raw)
    except (TypeError, ValueError):
        _log_entry("error", f"无法解析上游消息：{raw[:120]}")
        return
    ftype = frame.get("type")
    if ftype == "heartbeat":
        with _lock:
            _status["heartbeat_count"] += 1
            _status["last_heartbeat_at"] = datetime.now(timezone.utc).isoformat()
        _log_entry("heartbeat", "心跳")
    elif ftype == "treasury-repo-alert":
        content = frame.get("content") or {}
        title = str(content.get("title") or "逆回购利率提醒")
        body = str(content.get("body") or "")
        with _lock:
            _status["alert_count"] += 1
            _status["last_alert_at"] = datetime.now(timezone.utc).isoformat()
        _log_entry("alert", f"{title}：{body}", content)
        # 广播给全部在线用户（SSE /todos/stream，事件 repo/alerted）
        todo_bus.publish(todo_bus.online_user_ids(), "repo/alerted", content)
    else:
        _log_entry("error", f"未知上游消息类型：{ftype}")


def _run(url: str) -> None:
    from websockets.sync.client import connect
    import time as _time

    backoff = 5  # 断线重连退避：5s 起步指数递增至 60s 封顶；稳跑超 60s 才重置（握手成功即被断不算稳定）
    while True:
        opened_at = None
        _set(state="connecting", last_error=None)
        _log_entry("connect", f"连接上游 {url}")
        try:
            with connect(url, open_timeout=10, ping_interval=20) as ws:
                opened_at = _time.monotonic()
                _set(state="online", connected_at=datetime.now(timezone.utc).isoformat())
                _log_entry("connect", "上游连接成功")
                for raw in ws:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "replace")
                    _handle_message(raw)
            # 服务端正常关闭：也走断线重连
            raise ConnectionError("上游连接被关闭")
        except Exception as exc:  # noqa: BLE001 — 守护循环需吞一切异常保活
            with _lock:
                _status["reconnect_count"] += 1
            _set(state="disconnected", last_error=f"{type(exc).__name__}: {exc}")
            if opened_at is not None and _time.monotonic() - opened_at > 60:
                backoff = 5
            retry_at = _time.strftime("%H:%M:%S", _time.localtime(_time.time() + backoff))
            _log_entry("disconnect", f"连接断开，{retry_at} 后重试：{type(exc).__name__}: {exc}")
        _time.sleep(backoff)
        backoff = min(backoff * 2, 60)


def start() -> None:
    """启动 WS 守护线程（REPO_ALERT_WS_URL 未配置=保持关闭，不报错）。"""
    global _started
    url = getattr(settings, "repo_alert_ws_url", None)
    if _started or not url:
        return
    _started = True
    with _lock:
        _status.update(enabled=True, url=url, state="stopped")
    threading.Thread(target=_run, args=(url,), name="repo-alert-ws", daemon=True).start()
    logger.info("repo alerts stream daemon started: %s", url)
