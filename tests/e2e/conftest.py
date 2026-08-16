"""XinHere 全链路 E2E 公共设施。

- 走 nginx（默认 http://localhost:8095）打全链路；DB 直连 psycopg 做对账/op-log 抽查。
- SSE：httpx stream 手写解析 event:/data:/id:，collect_sse 返回帧列表。
- 测试数据标题一律带 [e2e] 前缀；平台事件/日志 append-only 不清理。
"""
from __future__ import annotations

import json
import os
import time
import uuid

import httpx
import psycopg
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8095")
BACKEND_URL = os.environ.get("E2E_BACKEND_URL", "http://127.0.0.1:8100")
API = "/api/v1"
DB_DSN = os.environ.get(
    "E2E_DB_DSN",
    "dbname=xinhere user=dbuser password=Siiit2026 host=localhost port=11000",
)
PASSWORD = "Xin@2026"
E2E = "[e2e]"
COMPANY_INV01 = "信投数科"  # inv01 演示主账号归属公司
PERIOD = "2026-08"

MODEL_TIMEOUT = 180.0  # 模型触发用例读超时（秒）


# ---------------------------------------------------------------- HTTP 封装


class Client:
    """薄 REST/SSE 封装：固定鉴权头、错误体断言、SSE 流。"""

    def __init__(self, base_url: str = BASE_URL, token: str | None = None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._c = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(30.0, read=MODEL_TIMEOUT),
        )

    def close(self) -> None:
        self._c.close()

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        return self._c.request(method, API + path, **kw)

    def get(self, path: str, **kw) -> httpx.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> httpx.Response:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw) -> httpx.Response:
        return self.request("PUT", path, **kw)

    def ok(self, method: str, path: str, expect: int = 200, **kw):
        r = self.request(method, path, **kw)
        assert r.status_code == expect, f"{method} {path} -> {r.status_code}: {r.text[:400]}"
        return r.json() if r.content else {}

    def err(self, method: str, path: str, expect: int, code: str, **kw) -> dict:
        """断言错误响应：状态码 + 封闭错误码。"""
        r = self.request(method, path, **kw)
        assert r.status_code == expect, f"{method} {path} -> {r.status_code}: {r.text[:400]}"
        body = r.json()
        assert body.get("code") == code, f"{method} {path} 错误码偏差: {body}"
        assert "X-Request-Id" in r.headers, "缺 X-Request-Id 响应头"
        return body

    def stream(self, method: str, path: str, **kw):
        return self._c.stream(method, API + path, **kw)


def login(username: str, password: str = PASSWORD) -> dict:
    r = httpx.post(
        f"{BASE_URL}{API}/auth/login",
        json={"username": username, "password": password},
        timeout=30.0,
    )
    assert r.status_code == 200, f"login {username} -> {r.status_code}: {r.text[:300]}"
    return r.json()


@pytest.fixture(scope="session")
def hq_auth() -> dict:
    return login("hq01")


@pytest.fixture(scope="session")
def inv_auth() -> dict:
    return login("inv01")


@pytest.fixture(scope="session")
def hq(hq_auth):
    c = Client(token=hq_auth["token"])
    yield c
    c.close()


@pytest.fixture(scope="session")
def inv(inv_auth):
    c = Client(token=inv_auth["token"])
    yield c
    c.close()


# ---------------------------------------------------------------- DB 帮助


@pytest.fixture()
def db():
    conn = psycopg.connect(DB_DSN, autocommit=True)
    yield conn
    conn.close()


def db_all(conn, sql: str, args: tuple = ()) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def db_one(conn, sql: str, args: tuple = ()):
    rows = db_all(conn, sql, args)
    return rows[0] if rows else None


def db_wait_one(conn, sql: str, args: tuple = (), timeout: float = 10.0, poll: float = 0.3):
    """轮询直到查询出首行。FastAPI yield 依赖在响应发送后才 commit，DB 断言需等待可见。"""
    deadline = time.monotonic() + timeout
    while True:
        row = db_one(conn, sql, args)
        if row is not None:
            return row
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


# ---------------------------------------------------------------- SSE 帮助


def parse_sse(lines) -> list[dict]:
    """手写 SSE 解析：event:/data:/id: → 帧 {event, id, data}；': hb' 注释跳过。"""
    frames: list[dict] = []
    event, fid, data_lines = None, None, []

    def _flush():
        nonlocal event, fid, data_lines
        if event is not None or data_lines:
            raw = "".join(data_lines)
            frames.append(
                {"event": event, "id": fid, "data": json.loads(raw) if raw else {}}
            )
        event, fid, data_lines = None, None, []

    for line in lines:
        if line == "":
            _flush()
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.lstrip(" ")
        if field == "event":
            event = value
        elif field == "data":
            data_lines.append(value)
        elif field == "id":
            fid = value
    _flush()
    return frames


def collect_sse(resp: httpx.Response, stop_events=("turn/end", "error"),
                max_frames: int | None = None) -> list[dict]:
    """消费一个 SSE 响应直到 stop_events / max_frames / 流结束，返回帧列表。"""
    frames: list[dict] = []
    event, fid, data_lines = None, None, []

    def _flush():
        nonlocal event, fid, data_lines
        if event is not None or data_lines:
            raw = "".join(data_lines)
            frames.append(
                {"event": event, "id": fid, "data": json.loads(raw) if raw else {}}
            )
        event, fid, data_lines = None, None, []

    for line in resp.iter_lines():
        if line == "":
            _flush()
            if frames and frames[-1]["event"] in stop_events:
                break
            if max_frames and len(frames) >= max_frames:
                break
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.lstrip(" ")
        if field == "event":
            event = value
        elif field == "data":
            data_lines.append(value)
        elif field == "id":
            fid = value
    _flush()
    return frames


def chat_collect(client: Client, session_id: str, message: str,
                 stop_events=("turn/end", "error"), max_frames: int | None = None,
                 read_timeout: float = MODEL_TIMEOUT) -> list[dict]:
    """POST chat 并收集 SSE 帧。"""
    with client.stream(
        "POST", f"/sessions/{session_id}/chat",
        json={"message": message},
        timeout=httpx.Timeout(30.0, read=read_timeout),
    ) as resp:
        if resp.status_code != 200:
            resp.read()
            raise AssertionError(f"chat -> {resp.status_code}: {resp.text[:400]}")
        return collect_sse(resp, stop_events=stop_events, max_frames=max_frames)


def new_session(client: Client, title: str) -> str:
    body = client.ok("POST", "/sessions", json={"title": f"{E2E} {title}"})
    assert body["domain"] == "general" and body["status"] == "active"
    return body["session_id"]


# ---------------------------------------------------------------- 事件回放帮助


def get_events_page(client: Client, session_id: str, after_seq: int = -1,
                    limit: int = 500) -> tuple[list[dict], bool]:
    body = client.ok(
        "GET", f"/sessions/{session_id}/events",
        params={"after_seq": after_seq, "limit": limit},
    )
    return body["items"], body["has_more"]


def list_all_events(client: Client, session_id: str) -> list[dict]:
    items: list[dict] = []
    after = -1
    while True:
        page, has_more = get_events_page(client, session_id, after_seq=after)
        items.extend(page)
        if not has_more or not page:
            return items
        after = page[-1]["seq"]


def wait_for_event(client: Client, session_id: str, predicate, timeout: float = MODEL_TIMEOUT,
                   poll: float = 2.0, after_seq: int = -1) -> tuple[dict, list[dict]]:
    """轮询 JSON 事件回放直到 predicate(item) 命中；返回 (命中帧, 当前全量)。"""
    deadline = time.monotonic() + timeout
    while True:
        items = list_all_events(client, session_id)
        for it in items:
            if it["seq"] > after_seq and predicate(it):
                return it, items
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"等待事件超时（{timeout}s）；当前帧型: {[i['type'] for i in items]}"
            )
        time.sleep(poll)


def assert_seq_continuous(seqs: list[int], start: int = 0) -> None:
    """seq 自 start 起连续无跳号。"""
    assert sorted(seqs) == list(range(start, start + len(seqs))), (
        f"seq 不连续: {sorted(seqs)[:20]}... (共 {len(seqs)})"
    )


# ---------------------------------------------------------------- 模型触发帮助


def chat_until_component(client: Client, title: str, message: str, kind: str,
                         attempts: int = 2) -> tuple[str, dict, list[dict]]:
    """模型 interrupt 触发：每轮新会话，直到 component/request[kind] 出现。

    返回 (session_id, component_request_data, 首轮帧列表)。
    模型始终不调用工具 → pytest.xfail（概率行为，禁止伪造工具调用）。
    """
    last_types: list[str] = []
    for attempt in range(1, attempts + 1):
        sid = new_session(client, f"{title}（第{attempt}轮）")
        frames = chat_collect(client, sid, message,
                              stop_events=("turn/end", "error", "component/request"))
        last_types = [f["event"] for f in frames]
        for fr in frames:
            if fr["event"] == "component/request" and fr["data"].get("kind") == kind:
                return sid, fr["data"], frames
    pytest.xfail(f"model did not call tool（{attempts} 轮均未出现 component/request[{kind}]，"
                 f"末轮帧型: {last_types}）")


def submit_component(client: Client, session_id: str, component_id: str,
                     action: str, interrupt_id: str, values: dict | None = None) -> dict:
    return client.ok(
        "POST", f"/sessions/{session_id}/components/{component_id}/submit",
        json={"action": action, "values": values, "interrupt_id": interrupt_id},
    )


def gen_id() -> str:
    return str(uuid.uuid4())


def wait_until(fn, timeout: float = 10.0, poll: float = 0.3):
    """轮询 fn 直到返回真值；超时返回最后一次值。用于跨请求 commit 可见性等待。"""
    deadline = time.monotonic() + timeout
    val = fn()
    while not val and time.monotonic() < deadline:
        time.sleep(poll)
        val = fn()
    return val
