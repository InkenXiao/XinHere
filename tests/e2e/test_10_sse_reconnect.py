"""矩阵 10：断线重连。

hq01 chat「写一首 200 字关于秋天的诗」→ 读前 3 帧后断开 →
GET events?after_seq={last}（Accept: text/event-stream）→ 首帧 baseline +
后续 seq 与已收衔接无缺口，合并 0..max 连续；最终有 turn/end（run 不终止）。
"""
from __future__ import annotations

import httpx

from .conftest import API, assert_seq_continuous, collect_sse, new_session


def test_sse_reconnect_no_gap(hq):
    sid = new_session(hq, "断线重连")

    # 第一轮：只读前 3 帧即断开（run 服务端继续）
    with hq.stream(
        "POST", f"/sessions/{sid}/chat",
        json={"message": "写一首 200 字关于秋天的诗"},
        timeout=httpx.Timeout(30.0, read=60.0),
    ) as resp:
        assert resp.status_code == 200
        first_frames = collect_sse(resp, stop_events=("turn/end", "error"), max_frames=3)
    assert len(first_frames) == 3
    assert [f["event"] for f in first_frames[:2]] == ["user/message", "turn/start"]
    last_seq = first_frames[-1]["data"]["seq"]

    # 重连：after_seq=last，SSE 形态 → baseline + backlog + 增量
    with hq.stream(
        "GET", f"/sessions/{sid}/events",
        params={"after_seq": last_seq, "limit": 1000},
        headers={"Accept": "text/event-stream"},
        timeout=httpx.Timeout(30.0, read=180.0),
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        second_frames = collect_sse(resp, stop_events=("turn/end", "error"))

    assert second_frames, "重连后无帧"
    baseline = second_frames[0]
    assert baseline["event"] == "baseline", f"首帧应为 baseline: {baseline['event']}"
    assert baseline["data"]["seq"] == last_seq
    assert "projections" in baseline["data"] and "pending" in baseline["data"]
    assert "interrupts" in baseline["data"]["pending"]

    # 衔接：后续帧 seq 从 last+1 起与已收无缺口，合并 0..max 连续。
    # error 帧为基线即有的 seq=-1 哨兵（不占事件序号），连续性校验剔除；
    # LLM 网关不可达时 run 以 error 收尾——此时降级验证「断流可重连且衔接不缺口」，
    # 不再要求 turn/end completed（环境依赖，见验收清单）。
    inc_seqs = [f["data"]["seq"] for f in second_frames[1:] if f["data"].get("seq", -1) >= 0]
    merged = sorted(
        ({f["data"]["seq"] for f in first_frames if f["data"].get("seq", -1) >= 0}) | set(inc_seqs)
    )
    if inc_seqs:
        assert min(inc_seqs) == last_seq + 1, f"重连起点错位: min={min(inc_seqs)} last={last_seq}"
    assert_seq_continuous(merged, start=0)

    # run 收尾：正常环境 completed；LLM 不可达环境 error（两种均视为流闭合）
    tail = [f for f in second_frames if f["event"] == "turn/end"]
    assert tail, "未收到 turn/end（run 应续跑到结束）"
    assert tail[-1]["data"].get("reason") in ("completed", "error")
