from __future__ import annotations

import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import errors
from ..persistence.models import PitReport


def view(r: PitReport) -> dict:
    return {
        "report_id": r.report_id,
        "company_ids": r.company_ids,
        "period": r.period,
        "outline": r.outline,
        "content": r.content,
        "status": r.status,
        "created_at": r.created_at.isoformat(),
    }


def create(db: Session, *, company_ids: list[str], period: str) -> PitReport:
    r = PitReport(company_ids=company_ids, period=period, status="outlining")
    db.add(r)
    db.flush()
    return r


def get(db: Session, report_id: str) -> dict:
    r = db.get(PitReport, report_id)
    if r is None:
        raise errors.not_found("报告不存在")
    return view(r)


def list_reports(db: Session) -> list[dict]:
    return [view(r) for r in db.scalars(select(PitReport).order_by(PitReport.created_at.desc())).all()]


def generate_async(report_id: str, emit=None) -> None:
    """后台线程生成大纲与正文；emit(section_idx, content) 可选事件回调。"""
    from ..persistence.session import SessionLocal

    def _run() -> None:
        from ..platform.agent.llm import chat_once

        with SessionLocal() as db:
            r = db.get(PitReport, report_id)
            if r is None:
                return
            try:
                outline_text = chat_once(
                    f"为投后管理报告生成大纲（5 节以内，每节一行短标题）。"
                    f"被投企业：{('、'.join(r.company_ids))}；归属期：{r.period}。只输出大纲行。"
                )
                outline = [ln.strip(" -・0123456789.") for ln in outline_text.splitlines() if ln.strip()][:6] or ["经营概览"]
                r.outline = outline
                r.status = "draft"
                db.commit()
                parts = []
                for idx, sec in enumerate(outline):
                    content = chat_once(
                        f"撰写投后管理报告小节「{sec}」。被投企业：{('、'.join(r.company_ids))}；"
                        f"归属期：{r.period}。200 字以内，正式公文语气。"
                    )
                    parts.append(f"## {sec}\n{content}")
                    r.content = "\n\n".join(parts)
                    db.commit()
                    if emit:
                        emit(idx, content)
                r.status = "done"
                db.commit()
            except Exception as exc:  # 生成失败留痕，不崩
                r.content = f"生成失败：{exc}"
                r.status = "draft"
                db.commit()

    threading.Thread(target=_run, name=f"pit-gen-{report_id[:8]}", daemon=True).start()
