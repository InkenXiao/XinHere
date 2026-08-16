from __future__ import annotations

from langchain_core.messages import AIMessage

from app.platform.agent.event_translator import EventTranslator
from app.platform.events.store import store


def test_flush_chunks_direct(mk_session):
    """批量路径：append_chunk ×3 → flush 得 3 条且 seq 连续；后续 append 紧接其后。"""
    sid = mk_session()
    for i in range(3):
        store.append_chunk(
            sid, {"turn": 1, "step": 1, "delta": f"d{i}", "version": 1}, turn=1
        )
    flushed = store.flush_chunks(sid)
    assert len(flushed) == 3
    seqs = [seq for seq, _t, _d in flushed]
    assert seqs == list(range(seqs[0], seqs[0] + 3))

    seq, _ = store.append(
        sid, "user/message",
        {"content": "[pytest] m", "source": "human", "version": 1},
        publish=False,
    )
    assert seq == seqs[-1] + 1


def test_translator_pairing(mk_session):
    """翻译器路径：chunk 先落、assistant/message 后落且 source_event_seqs 完成配对。"""
    sid = mk_session()
    tr = EventTranslator(store, sid, turn=1)
    tr.handle_chunk(AIMessage(content="你好，"), {})
    tr.handle_chunk(AIMessage(content="世界"), {})
    tr.finalize()

    rows, _ = store.list_events(sid, limit=100)
    types = [r.type for r in rows]
    assert types == [
        "step/start", "assistant/chunk", "assistant/chunk",
        "assistant/message", "step/end",
    ]

    msg = rows[3]
    chunk_seqs = [rows[1].seq, rows[2].seq]
    assert msg.data["content"] == "你好，世界"
    assert msg.data["source_event_seqs"] == chunk_seqs
    assert all(s < msg.seq for s in msg.data["source_event_seqs"])


def test_translator_pairing_with_mid_flush(mk_session):
    """flusher 中途批量落库（200ms 触发）后 finalize：source_event_seqs 仍覆盖全部 chunk。"""
    sid = mk_session()
    tr = EventTranslator(store, sid, turn=1)
    for i in range(5):
        tr.handle_chunk(AIMessage(content=f"早段{i}"), {})
    # 模拟 flusher 线程中途批量落库：走内部 flush 路径，seq 进累积区而非返回值
    store._flush_and_record(sid)
    for i in range(3):
        tr.handle_chunk(AIMessage(content=f"尾段{i}"), {})
    tr.finalize()

    rows, _ = store.list_events(sid, limit=100)
    chunks = [r for r in rows if r.type == "assistant/chunk"]
    msgs = [r for r in rows if r.type == "assistant/message"]
    assert len(chunks) == 8
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.data["content"] == "".join(f"早段{i}" for i in range(5)) + "".join(
        f"尾段{i}" for i in range(3)
    )
    # 全覆盖：累积区（前 5）+ 队列残余（后 3），且均小于 message seq
    assert sorted(msg.data["source_event_seqs"]) == sorted(r.seq for r in chunks)
    assert len(msg.data["source_event_seqs"]) == 8
    assert all(s < msg.seq for s in msg.data["source_event_seqs"])
    # turn 边界已清理：累积区不留滞
    assert sid not in store._flushed_seqs
