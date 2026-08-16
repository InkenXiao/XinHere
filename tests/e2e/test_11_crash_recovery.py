"""矩阵 11：崩溃恢复。

run 进行中重启 backend 容器 → 重启后补 turn/end{reason:'crashed'}；
若存在未闭合 tool/call，则对应 tool/result{outcome:'unknown'}。
用例结束必须确认 stack 恢复 healthy。
"""
from __future__ import annotations

import subprocess
import time

import httpx

from .conftest import (
    API, BACKEND_URL, Client, collect_sse, list_all_events, new_session,
)


def _wait_healthy(timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{BACKEND_URL}/healthz", timeout=5.0)
            if r.status_code == 200:
                return True
        except httpx.TransportError:
            pass
        time.sleep(2.0)
    return False


def test_crash_recovery_closes_run(hq, db):
    sid = new_session(hq, "[e2e] 崩溃恢复")

    # 长文 run：收到 step/start 立即重启容器（模型生成期必然覆盖重启窗口）
    with hq.stream(
        "POST", f"/sessions/{sid}/chat",
        json={"message": "写一篇不少于 2000 字关于人工智能发展史的长文，分章节详细展开。"},
        timeout=httpx.Timeout(30.0, read=180.0),
    ) as resp:
        assert resp.status_code == 200, f"chat -> {resp.status_code}"
        collect_sse(resp, stop_events=("step/start",), max_frames=3)

    subprocess.run(["docker", "restart", "xinhere-backend"], check=True,
                   capture_output=True, timeout=60)
    assert _wait_healthy(), "backend 重启后 90s 内未恢复 healthy"

    # 启动补偿：turn/end{reason:'crashed'}（启动时同步写入，轮询保险 30s）
    items, deadline = [], time.monotonic() + 30
    while time.monotonic() < deadline:
        items = list_all_events(hq, sid)
        ends = [it for it in items if it["type"] == "turn/end"]
        if ends:
            assert ends[-1]["data"].get("reason") == "crashed", (
                f"turn/end reason 偏差: {ends[-1]['data']}"
            )
            break
        time.sleep(2.0)
    else:
        raise AssertionError(
            f"30s 内未补 turn/end；当前帧型: {[i['type'] for i in items]}"
        )

    # 未闭合 tool/call → tool/result{outcome:'unknown'}（本场景一般无工具，有条件才断言）
    calls = {it["data"]["call_id"] for it in items if it["type"] == "tool/call"}
    results = {it["data"]["call_id"] for it in items if it["type"] == "tool/result"}
    dangling = calls - results
    if dangling:
        unknowns = {
            it["data"]["call_id"] for it in items
            if it["type"] == "tool/result" and it["data"].get("outcome") == "unknown"
        }
        # 补偿结果应已落库（在上面 turn/end 之前一并写入），重新拉一遍再判
        items = list_all_events(hq, sid)
        unknowns = {
            it["data"]["call_id"] for it in items
            if it["type"] == "tool/result" and it["data"].get("outcome") == "unknown"
        }
        assert dangling <= unknowns, f"未闭合工具未补偿: {dangling - unknowns}"

    # stack 恢复确认（后续用例依赖）
    assert _wait_healthy(30.0), "用例结束时 stack 非 healthy"
