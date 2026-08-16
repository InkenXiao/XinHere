from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import errors
from ..persistence.models import RiskFillBatch, RiskFillItem, RiskFillReport
from . import todo as todo_svc
from .common import company_user

# 16 项风险指标模板（与原型 RISK_ITEMS 对齐；pf=true 为系统预填只读）
RISK_ITEM_TEMPLATE: list[dict] = [
    {"name": "主营业务收入", "fields": [{"k": "主营业务收入", "v": ""}, {"k": "主营业务收入预算值", "v": "13,000", "pf": True}]},
    {"name": "归母净利润", "fields": [{"k": "归母净利润", "v": ""}, {"k": "归母净利润预算值", "v": "2,400", "pf": True}]},
    {"name": "经营活动净现金流(不含资金集中)", "fields": [{"k": "经营活动净现金流(含集中)", "v": ""}, {"k": "资金集中金额(净现金流)", "v": ""}, {"k": "经营活动净现金流预算值", "v": "1,500", "pf": True}]},
    {"name": "资产负债率", "fields": [{"k": "资产总额", "v": ""}, {"k": "负债总额", "v": ""}]},
    {"name": "有息资产负债率", "fields": [{"k": "期末有息负债", "v": ""}, {"k": "主营业务收入", "v": ""}]},
    {"name": "现金保障倍数", "fields": [{"k": "可用货币资金余额(扣集中)", "v": ""}, {"k": "资金集中金额(可用货币资金)", "v": ""}, {"k": "当年预算现金支出", "v": ""}, {"k": "经营活动净现金流(含集中)", "v": ""}, {"k": "资金集中金额(净现金流)", "v": ""}]},
    {"name": "现金到期债务比", "fields": [{"k": "可用货币资金余额(扣集中)", "v": ""}, {"k": "资金集中金额(可用货币资金)", "v": ""}, {"k": "6个月内到期有息负债总计", "v": ""}]},
    {"name": "高风险担保清单", "fields": [{"k": "高风险担保清单", "v": ""}]},
    {"name": "担保净资产比", "fields": [{"k": "担保余额", "v": ""}, {"k": "融资担保规模", "v": ""}, {"k": "企业合并净资产", "v": ""}, {"k": "企业单体净资产", "v": ""}]},
    {"name": "存货周转率", "fields": [{"k": "主营业务成本", "v": ""}, {"k": "存货值(含跌价准备)", "v": ""}]},
    {"name": "应收账款+合同资产周转率", "fields": [{"k": "主营业务收入", "v": ""}, {"k": "应收账款(含坏账准备)", "v": ""}, {"k": "合同资产", "v": ""}]},
    {"name": "毛利率", "fields": [{"k": "主营业务收入", "v": ""}, {"k": "主营业务成本", "v": ""}]},
    {"name": "营业现金比率", "fields": [{"k": "经营活动净现金流(含集中)", "v": ""}, {"k": "资金集中金额(净现金流)", "v": ""}, {"k": "主营业务收入", "v": ""}]},
    {"name": "主业利润占总利润比重", "fields": [{"k": "本年累计营业利润", "v": ""}, {"k": "公允价值变动损益", "v": ""}, {"k": "投资收益", "v": ""}, {"k": "与主业相关投资收益", "v": ""}, {"k": "与主业相关营业外收入", "v": ""}, {"k": "利润总额", "v": ""}]},
    {"name": "企业举债经营", "fields": [{"k": "有息负债", "v": ""}, {"k": "归母净利润", "v": ""}]},
    {"name": "企业商誉或投资标的减值", "fields": [{"k": "商誉减值规模", "v": ""}, {"k": "企业合并净资产", "v": ""}, {"k": "对赌条款", "v": ""}, {"k": "投资标的公允价值", "v": ""}, {"k": "实际出资成本", "v": ""}]},
]


def batch_view(b: RiskFillBatch) -> dict:
    return {
        "batch_id": b.batch_id,
        "period": b.period,
        "dispatcher_id": b.dispatcher_id,
        "status": b.status,
        "created_at": b.created_at.isoformat(),
    }


def report_view(db: Session, r: RiskFillReport, with_items: bool = True) -> dict:
    out = {
        "report_id": r.report_id,
        "batch_id": r.batch_id,
        "company": r.company,
        "status": r.status,
        "lamp_r": r.lamp_r,
        "lamp_y": r.lamp_y,
        "lamp_g": r.lamp_g,
    }
    if with_items:
        items = db.scalars(
            select(RiskFillItem).where(RiskFillItem.report_id == r.report_id).order_by(RiskFillItem.idx)
        ).all()
        out["items"] = [{"idx": i.idx, "name": i.name, "lamp": i.lamp, "fields": i.fields} for i in items]
    return out


def create_batch(db: Session, *, period: str, companies: list[str], dispatcher_id: str) -> RiskFillBatch:
    batch = RiskFillBatch(period=period, dispatcher_id=dispatcher_id)
    db.add(batch)
    db.flush()
    task = todo_svc.create_task(
        db, scene="risk_fill", title=f"风险预警财务指标填报（{period}）",
        dispatcher_id=dispatcher_id, payload={"batch_id": batch.batch_id}, period=period,
    )
    for company in companies:
        report = RiskFillReport(batch_id=batch.batch_id, company=company, lamp_g=len(RISK_ITEM_TEMPLATE))
        db.add(report)
        db.flush()
        for idx, tpl in enumerate(RISK_ITEM_TEMPLATE, start=1):
            db.add(RiskFillItem(report_id=report.report_id, idx=idx, name=tpl["name"], lamp="g", fields=tpl["fields"]))
        user = company_user(db, company)
        if user:
            todo_svc.create_todo(
                db, task=task, assignee_id=user.user_id, kind="action",
                title=f"{company} · 风险预警财务指标填报",
                sub=f"归属期：{period} · 待反馈",
                ref={"batch_id": batch.batch_id, "company": company, "report_id": report.report_id},
            )
    db.flush()
    return batch


def list_batches(db: Session) -> list[dict]:
    rows = db.scalars(select(RiskFillBatch).order_by(RiskFillBatch.created_at.desc())).all()
    return [batch_view(b) for b in rows]


def get_batch(db: Session, batch_id: str) -> dict:
    b = db.get(RiskFillBatch, batch_id)
    if b is None:
        raise errors.not_found("批次不存在")
    reports = db.scalars(
        select(RiskFillReport).where(RiskFillReport.batch_id == batch_id).order_by(RiskFillReport.company)
    ).all()
    return {**batch_view(b), "reports": [report_view(db, r, with_items=False) for r in reports]}


def get_report(db: Session, batch_id: str, company: str) -> dict:
    r = _get_report(db, batch_id, company)
    return report_view(db, r)


def _get_report(db: Session, batch_id: str, company: str) -> RiskFillReport:
    r = db.scalars(
        select(RiskFillReport).where(
            RiskFillReport.batch_id == batch_id, RiskFillReport.company == company
        )
    ).first()
    if r is None:
        raise errors.not_found("填报单不存在")
    return r


def save_items(db: Session, batch_id: str, company: str, items: list[dict]) -> dict:
    r = _get_report(db, batch_id, company)
    if r.status == "reviewed":
        raise errors.validation("已审批，不可修改")
    existing = {
        i.idx: i
        for i in db.scalars(select(RiskFillItem).where(RiskFillItem.report_id == r.report_id)).all()
    }
    for item in items:
        row = existing.get(int(item["idx"]))
        if row is None:
            continue
        # pf 预填字段只读：保留原值
        merged = []
        old_pf = {f["k"]: f["v"] for f in row.fields if f.get("pf")}
        for f in item.get("fields", []):
            if f["k"] in old_pf:
                merged.append({"k": f["k"], "v": old_pf[f["k"]], "pf": True})
            else:
                merged.append({"k": f["k"], "v": f.get("v", "")})
        row.fields = merged
        if item.get("lamp") in ("r", "y", "g"):
            row.lamp = item["lamp"]
    db.flush()
    _recount_lamps(db, r)
    return report_view(db, r)


def _recount_lamps(db: Session, r: RiskFillReport) -> None:
    rows = db.execute(
        select(RiskFillItem.lamp, func.count())
        .where(RiskFillItem.report_id == r.report_id)
        .group_by(RiskFillItem.lamp)
    ).all()
    counts = {k: v for k, v in rows}
    r.lamp_r = counts.get("r", 0)
    r.lamp_y = counts.get("y", 0)
    r.lamp_g = counts.get("g", 0)
    db.flush()


def submit(db: Session, batch_id: str, company: str) -> dict:
    r = _get_report(db, batch_id, company)
    if r.status != "unfilled":
        raise errors.validation(f"当前状态 {r.status} 不可提交")
    r.status = "filled"
    _recount_lamps(db, r)
    batch = db.get(RiskFillBatch, batch_id)
    if batch:
        from ..persistence.models import SysUser
        from sqlalchemy import select as s

        hq = db.scalars(s(SysUser).where(SysUser.user_id == batch.dispatcher_id)).first()
        if hq:
            task = todo_svc.create_task(
                db, scene="risk_fill", title=f"风险填报审批（{company}）",
                dispatcher_id=batch.dispatcher_id, payload={"batch_id": batch_id}, period=batch.period,
            )
            todo_svc.create_todo(
                db, task=task, assignee_id=batch.dispatcher_id, kind="review",
                title=f"{company} · 风险填报审批", sub=f"归属期：{batch.period}",
                ref={"batch_id": batch_id, "company": company, "report_id": r.report_id},
            )
    db.flush()
    return report_view(db, r)


def review(db: Session, batch_id: str, company: str, approve: bool, comment: str | None) -> dict:
    r = _get_report(db, batch_id, company)
    if r.status != "filled":
        raise errors.validation("仅已填报状态可审批")
    r.status = "reviewed" if approve else "unfilled"
    db.flush()
    return report_view(db, r)


def status_counts(db: Session, batch_id: str | None = None) -> dict:
    if batch_id is None:
        b = db.scalars(select(RiskFillBatch).order_by(RiskFillBatch.created_at.desc()).limit(1)).first()
        if b is None:
            return {}
        batch_id = b.batch_id
    rows = db.execute(
        select(RiskFillReport.status, func.count())
        .where(RiskFillReport.batch_id == batch_id)
        .group_by(RiskFillReport.status)
    ).all()
    counts = {k: v for k, v in rows}
    return {
        "batch_id": batch_id,
        "unfilled": counts.get("unfilled", 0),
        "filled": counts.get("filled", 0),
        "reviewed": counts.get("reviewed", 0),
    }
