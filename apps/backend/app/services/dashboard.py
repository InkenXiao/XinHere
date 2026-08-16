from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..persistence.models import (
    BizTask,
    BizTodo,
    RiskFillBatch,
    RiskFillItem,
    RiskFillReport,
)


def summary(db: Session) -> dict:
    open_tasks = db.scalar(select(func.count()).select_from(BizTask).where(BizTask.status == "open"))
    completed_7d = db.scalar(
        select(func.count())
        .select_from(BizTodo)
        .where(BizTodo.status == "completed", BizTodo.updated_at >= func.now() - func.make_interval(0, 0, 0, 7))
    )
    total_todos = db.scalar(select(func.count()).select_from(BizTodo)) or 0
    completed_total = db.scalar(
        select(func.count()).select_from(BizTodo).where(BizTodo.status == "completed")
    ) or 0
    overdue = db.scalar(
        select(func.count())
        .select_from(BizTodo)
        .where(BizTodo.due < func.now(), BizTodo.status.in_(["pending", "na_pending", "feedback_submitted", "submitted"]))
    )

    scene_rows = db.execute(select(BizTask.scene, BizTask.status, func.count()).group_by(BizTask.scene, BizTask.status)).all()
    scene_map: dict[str, dict] = {}
    for scene, status, cnt in scene_rows:
        slot = scene_map.setdefault(scene, {"scene": scene, "total": 0, "done": 0})
        slot["total"] += cnt
        if status == "closed":
            slot["done"] += cnt

    funnel_rows = db.execute(select(BizTodo.status, func.count()).group_by(BizTodo.status)).all()
    funnel = [{"status": s, "count": c} for s, c in sorted(funnel_rows)]

    risk_board = None
    batch = db.scalars(select(RiskFillBatch).order_by(RiskFillBatch.created_at.desc()).limit(1)).first()
    if batch is not None:
        reports = db.scalars(select(RiskFillReport).where(RiskFillReport.batch_id == batch.batch_id)).all()
        lamp_rows = db.execute(
            select(RiskFillItem.lamp, func.count())
            .join(RiskFillReport, RiskFillItem.report_id == RiskFillReport.report_id)
            .where(RiskFillReport.batch_id == batch.batch_id)
            .group_by(RiskFillItem.lamp)
        ).all()
        lamps = {k: v for k, v in lamp_rows}
        risk_board = {
            "batch_id": batch.batch_id,
            "period": batch.period,
            "companies": [{"company": r.company, "status": r.status} for r in reports],
            "lamps": {"r": lamps.get("r", 0), "y": lamps.get("y", 0), "g": lamps.get("g", 0)},
        }

    today = date.today()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    created_rows = dict(
        db.execute(
            select(func.date(BizTodo.created_at), func.count()).group_by(func.date(BizTodo.created_at))
        ).all()
    )
    completed_rows = dict(
        db.execute(
            select(func.date(BizTodo.updated_at), func.count())
            .where(BizTodo.status == "completed")
            .group_by(func.date(BizTodo.updated_at))
        ).all()
    )
    trend = [
        {"date": d.isoformat(), "created": int(created_rows.get(d, 0)), "completed": int(completed_rows.get(d, 0))}
        for d in days
    ]

    return {
        "overview": {
            "open_tasks": int(open_tasks or 0),
            "completed_7d": int(completed_7d or 0),
            "completion_rate": round(completed_total / total_todos, 4) if total_todos else 0.0,
            "overdue": int(overdue or 0),
        },
        "by_scene": list(scene_map.values()),
        "todo_funnel": funnel,
        "risk_board": risk_board,
        "trend_14d": trend,
    }
