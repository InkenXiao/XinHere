from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.platform.events.store import store

THREADS = 8
PER_THREAD = 25


def test_concurrent_append_seq_no_gap(mk_session):
    """8 线程 × 25 次 append：seq 必须连续、无重复、无缺口。"""
    sid = mk_session()

    def worker(i: int) -> None:
        for j in range(PER_THREAD):
            store.append(
                sid,
                "feedback/record",
                {"text": f"[pytest] t{i}-{j}", "version": 1},
                publish=False,
            )

    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        list(ex.map(worker, range(THREADS)))

    total = THREADS * PER_THREAD
    assert store.max_seq(sid) == total - 1
    rows, has_more = store.list_events(sid, limit=1000)
    assert not has_more
    seqs = [r.seq for r in rows]
    assert len(seqs) == total
    assert sorted(seqs) == list(range(total))
