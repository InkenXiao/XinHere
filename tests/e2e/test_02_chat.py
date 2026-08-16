"""矩阵 2：纯文字对话。

hq01 建会话 → chat「你好，请用一句话介绍你自己」→
帧序 turn/start→step/start→assistant/chunk×N→assistant/message→step/end→turn/end；
seq 0..max 连续；source_event_seqs 数量 == chunk 帧数；GET events JSON 回放同 seq 集合。
"""
from __future__ import annotations

from .conftest import (
    assert_seq_continuous,
    chat_collect,
    list_all_events,
    new_session,
)


def test_plain_text_chat_frame_flow(hq):
    sid = new_session(hq, "纯文字对话")
    frames = chat_collect(hq, sid, "你好，请用一句话介绍你自己")

    types = [f["event"] for f in frames]
    assert types[0] == "user/message", f"首帧应为 user/message: {types[:5]}"
    assert types[1] == "turn/start"
    assert types[-1] == "turn/end", f"末帧应为 turn/end: {types[-3:]}"
    assert frames[-1]["data"].get("reason") == "completed"

    # 结构：turn/start 之后为 (step/start → chunk×N → assistant/message → step/end)×M
    body = types[2:-1]
    pos = 0
    steps = 0
    chunk_total = 0
    paired_total = 0
    while pos < len(body):
        assert body[pos] == "step/start", f"step 边界错乱: {types}"
        pos += 1
        n_chunk = 0
        while pos < len(body) and body[pos] == "assistant/chunk":
            n_chunk += 1
            pos += 1
        assert n_chunk >= 1, "step 内至少 1 条 chunk"
        assert body[pos] == "assistant/message", f"chunk 后应为 assistant/message: {types}"
        msg = frames[2 + pos]["data"]
        assert len(msg.get("source_event_seqs") or []) == n_chunk, (
            f"source_event_seqs({len(msg.get('source_event_seqs') or [])}) != chunk 帧数({n_chunk})"
        )
        assert msg.get("content"), "assistant/message content 非空"
        chunk_total += n_chunk
        paired_total += len(msg["source_event_seqs"])
        pos += 1
        assert body[pos] == "step/end", f"assistant/message 后应为 step/end: {types}"
        pos += 1
        steps += 1
    assert steps >= 1
    assert paired_total == chunk_total

    # seq 0..max 连续（id 形如 session_id:seq）
    seqs = [f["data"]["seq"] for f in frames]
    assert_seq_continuous(seqs, start=0)
    for f in frames:
        assert f["id"] == f"{sid}:{f['data']['seq']}"

    # GET events JSON 回放：同 seq 集合
    items = list_all_events(hq, sid)
    assert [i["seq"] for i in items] == sorted(seqs)
    replay_types = [i["type"] for i in items]
    assert replay_types == types, "回放帧型序列与 SSE 实收不一致"
