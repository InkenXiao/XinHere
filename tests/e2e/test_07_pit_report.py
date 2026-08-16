"""矩阵 7：投后报告（模型路径）。

hq01 对话 → 模型调 generate_post_report → pit/report-start 事件拿 report_id
→ 轮询 GET /reports/{id} 至 done → outline/content 落库非空。
"""
from __future__ import annotations

import pytest

from .conftest import (
    COMPANY_INV01, MODEL_TIMEOUT, PERIOD, chat_collect, db_one, new_session,
    wait_for_event, wait_until,
)


def test_pit_report_generation(hq, db):
    sid, report_id = None, None
    for attempt in (1, 2):  # 模型概率行为：最多两轮，不调用工具则 xfail
        sid = new_session(hq, f"[e2e] 投后报告（第{attempt}轮）")
        frames = chat_collect(
            hq, sid,
            f"立即调用 generate_post_report 工具，company_ids=[\"{COMPANY_INV01}\"]，"
            f"period={PERIOD}，生成投后报告。",
        )
        if any(f["event"] == "tool/call" and f["data"].get("name") == "generate_post_report"
               for f in frames):
            start, _ = wait_for_event(
                hq, sid, lambda it: it["type"] == "pit/report-start",
                timeout=MODEL_TIMEOUT,
            )
            report_id = start["data"]["report_id"]
            # report-start 为起始帧（outline 初始为空），大纲在 done 态校验
            break
    if not report_id:
        pytest.xfail("model did not call tool（两轮均未调用 generate_post_report）")

    # 异步生成 → 轮询至 done（5s×60=300s 上限）
    def _done():
        body = hq.ok("GET", f"/reports/{report_id}")
        return body if body.get("status") == "done" else None

    report = wait_until(_done, timeout=300.0, poll=5.0)
    assert report, f"报告 300s 内未完成（report_id={report_id}）"
    assert report["outline"], "outline 为空"
    assert report["content"], "content 为空"

    row = db_one(db, "SELECT status FROM pit_reports WHERE report_id=%s", (report_id,))
    assert row and row[0] == "done"
