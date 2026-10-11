"""AI 生成 PPT 报告（对话工具 generate_fin_risk_report 的领域服务）。

数据原则：财务风险报告的全部数字来自 risk 填报真实库值（RiskFillBatch/Report/Item），
LLM 只负责把确定性统计组织成 3-4 条执行摘要要点（失败回退确定性文案），绝不生成数字。

链路：risk 数据 → 确定性骨架 PptSpec（封面/总览/明细表/红灯清单）+ LLM 摘要页
→ services/ppt.render 渲染 .pptx → 对象存储（ppt-reports/{id}.pptx，无 DB 回退，
写失败 fail-loud）→ ppt_reports 行（spec JSONB 留档）→ 会话内 file/record 文件卡。
"""
from __future__ import annotations

import logging
import json
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import errors
from ..persistence.models import PptReport, RiskFillBatch, RiskFillItem, RiskFillReport
from . import storage
from .ppt import PptSpec, SlideSpec, TableSpec, render

logger = logging.getLogger(__name__)

_FILL_STATUS = {"unfilled": "未填", "filled": "已提交", "reviewed": "已审批"}


def collect_fin_risk(db: Session, period: str) -> dict | None:
    """收集归属期最新批次的填报数据；无批次返回 None，全未填返回空 reports 语义由调用方判断。"""
    batch = db.scalar(
        select(RiskFillBatch)
        .where(RiskFillBatch.period == period)
        .order_by(RiskFillBatch.created_at.desc())
        .limit(1)
    )
    if batch is None:
        return None
    reports = db.scalars(
        select(RiskFillReport)
        .where(RiskFillReport.batch_id == batch.batch_id)
        .order_by(RiskFillReport.company)
    ).all()
    return {"batch": batch, "reports": reports}


def _red_items(db: Session, report_id: str) -> list[str]:
    return [
        i.name
        for i in db.scalars(
            select(RiskFillItem).where(RiskFillItem.report_id == report_id, RiskFillItem.lamp == "r")
        ).all()
    ]


def build_fin_risk_spec(db: Session, period: str, data: dict) -> tuple[PptSpec, dict]:
    """risk 真实数据 → 确定性骨架 PptSpec + 统计（供 LLM 摘要与工具文案复用）。"""
    reports: list[RiskFillReport] = data["reports"]
    filled = [r for r in reports if r.status != "unfilled"]
    total_r = sum(r.lamp_r for r in reports)
    total_y = sum(r.lamp_y for r in reports)
    total_g = sum(r.lamp_g for r in reports)
    red_companies = [(r.company, _red_items(db, r.report_id)) for r in reports if r.lamp_r > 0]
    stats = {
        "period": period, "companies": len(reports), "filled": len(filled),
        "red": total_r, "yellow": total_y, "green": total_g,
        "red_companies": [c for c, _ in red_companies],
    }
    today = datetime.now().strftime("%Y-%m-%d")
    slides = [
        SlideSpec(layout="cover", title=f"财务风险报告（{period}）",
                  subtitle="风险预警财务指标汇总", notes=f"生成日期：{today}"),
        SlideSpec(layout="bullets", title="一、总体情况", bullets=[
            f"参报企业 {len(reports)} 家，已提交 {len(filled)} 家"
            f"（已审批 {sum(1 for r in reports if r.status == 'reviewed')} 家）",
            f"全量指标灯色：红 {total_r} 项 / 黄 {total_y} 项 / 绿 {total_g} 项",
            f"存在红灯指标的企业 {len(red_companies)} 家"
            + (f"：{'、'.join(c for c, _ in red_companies[:6])}" if red_companies else ""),
        ]),
        SlideSpec(layout="table", title="二、企业风险总览",
                  table=TableSpec(
                      headers=["企业", "填报状态", "红", "黄", "绿"],
                      rows=[[r.company, _FILL_STATUS.get(r.status, r.status),
                             str(r.lamp_r), str(r.lamp_y), str(r.lamp_g)] for r in reports],
                  )),
    ]
    if red_companies:
        slides.append(SlideSpec(layout="bullets", title="三、红灯指标明细", bullets=[
            f"{c}：{'、'.join(items)}" for c, items in red_companies
        ] + (["（其余企业无红灯指标）"] if len(red_companies) < len(reports) else [])))
    spec = PptSpec(title=f"财务风险报告（{period}）", subtitle="风险预警财务指标汇总", slides=slides)
    return spec, stats


def add_summary_slide(spec: PptSpec, stats: dict, llm_fn=None) -> None:
    """在结尾页前插入 LLM 执行摘要页；llm_fn 可注入（测试），失败/为空回退确定性文案。"""
    lines = None
    if llm_fn is not None:
        try:
            raw = llm_fn(
                f"你是财务风险分析师。以下是 {stats['period']} 风险填报统计（数字为准，不得改动或编造）："
                f"参报企业 {stats['companies']} 家、已提交 {stats['filled']} 家；"
                f"红 {stats['red']} 项、黄 {stats['yellow']} 项、绿 {stats['green']} 项；"
                f"红灯企业：{'、'.join(stats['red_companies']) or '无'}。"
                f"请输出 3-4 条一行式执行摘要要点（聚焦风险研判与建议动作），每条一行，不要编号、不要多余解释。"
            )
            lines = [ln.strip("-•· ") for ln in raw.splitlines() if ln.strip()][:4] or None
        except Exception:
            logger.warning("PPT 摘要 LLM 生成失败（回退确定性文案）")
    if not lines:
        lines = [
            f"本期参报 {stats['companies']} 家、已提交 {stats['filled']} 家，红/黄/绿指标 "
            f"{stats['red']}/{stats['yellow']}/{stats['green']} 项。",
            "建议对红灯企业逐项核实风险成因并跟踪下期改善。",
        ]
    idx = next((i for i, s in enumerate(spec.slides) if s.layout == "end"), len(spec.slides))
    spec.slides.insert(idx, SlideSpec(layout="bullets", title="执行摘要", bullets=lines))


def generate_fin_risk(db: Session, *, session_id: str | None, period: str,
                      llm_fn=None) -> dict:
    """端到端编排：取数 → spec → 摘要 → 落库 → 渲染存储。

    返回 {"row", "stats", "file_name"}；不可生成时返回 {"error": 提示文案}。
    工具 generate_fin_risk_report 与 report-confirm 组件确认共用此入口。"""
    data = collect_fin_risk(db, period)
    if data is None:
        return {"error": f"{period} 没有风险填报批次，无法生成报告，需先在该期间下发风险填报任务"}
    if not any(r.status != "unfilled" for r in data["reports"]):
        return {"error": f"{period} 已下发填报但尚无企业提交，暂无数据可出报告，请等待企业填报提交"}
    spec, stats = build_fin_risk_spec(db, period, data)
    add_summary_slide(spec, stats, llm_fn=llm_fn)
    title = f"财务风险报告（{period}）"
    file_name = f"财务风险报告-{period}.pptx"
    row = create_report(db, session_id=session_id, title=title, period=period,
                        file_name=file_name, spec=spec)
    render_and_store(spec, str(row.report_id))
    logger.info("财务风险报告 PPT 已生成 period=%s report_id=%s 企业=%d 红/黄/绿=%d/%d/%d",
                period, row.report_id, stats["companies"],
                stats["red"], stats["yellow"], stats["green"])
    return {"row": row, "stats": stats, "file_name": file_name}


def generate_generic(db: Session, *, session_id: str | None, title: str,
                     requirement: str = "", llm_fn=None) -> PptReport:
    """通用主题 PPT：LLM 产出 PptSpec JSON（纯内容组织，无业务数据绑定）→ 渲染落库。

    LLM 失败/输出不合法时回退最小可用骨架（封面+要点+结尾），保证始终出文件。
    与 generate_fin_risk 的区别：内容允许 LLM 自由组织（介绍/培训/总结类主题，
    不涉及业务库数字），护栏口径对齐 generate_html_report。"""
    spec = _spec_from_llm(title, requirement, llm_fn)
    file_name = f"{title}.pptx"
    row = create_report(db, session_id=session_id, title=title, period="",
                        file_name=file_name, spec=spec)
    render_and_store(spec, str(row.report_id))
    logger.info("通用 PPT 已生成 report_id=%s 标题=%s 页数=%d",
                row.report_id, title, len(spec.slides))
    return row


def _PPT_PROMPT(title: str, requirement: str) -> str:  # noqa: N802
    return (
        f"请为主题「{title}」设计一份 PPT 大纲并直接输出 JSON（不要 markdown 代码块、不要解释）。"
        f"内容要求：{requirement or '面向业务读者的介绍/汇报材料'}。"
        "JSON 结构（严格遵守）："
        '{"title":"...","subtitle":"...","slides":['
        '{"layout":"cover|agenda|section|bullets|table|end","title":"...",'
        '"subtitle":"...","bullets":["..."],"table":{"headers":["..."],"rows":[["..."]]},'
        '"notes":"..."}]}。'
        "规则：8-12 页；首页 cover、次页 agenda、末页 end；bullets 每页 3-5 条、每条一行不超过 30 字；"
        "表格页最多 1-2 页且不超过 10 行；中文；不编造具体财务数字（可用定性表述）。"
    )


def _spec_from_llm(title: str, requirement: str, llm_fn) -> PptSpec:
    if llm_fn is not None:
        try:
            raw = llm_fn(_PPT_PROMPT(title, requirement))
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.lower().startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                spec = PptSpec.model_validate(json.loads(raw[start:end + 1]))
                if not any(s.layout == "cover" for s in spec.slides):
                    spec.slides.insert(0, SlideSpec(layout="cover", title=spec.title or title))
                return spec
        except Exception:
            logger.warning("通用 PPT 大纲 LLM 生成失败（回退最小骨架）", exc_info=True)
    return PptSpec(
        title=title,
        slides=[
            SlideSpec(layout="cover", title=title, subtitle=requirement or "汇报材料"),
            SlideSpec(layout="bullets", title="内容要点", bullets=[
                "本页为占位骨架：大纲生成服务暂不可用",
                "可重新发起生成以获得完整大纲",
            ]),
            SlideSpec(layout="end", title="谢谢"),
        ],
    )


def artifact_key(report_id: str) -> str:
    return f"ppt-reports/{report_id}.pptx"


def store_artifact(report_id: str, pptx_bytes: bytes) -> None:
    """二进制权威副本写对象存储（MinIO，未配置降级本地磁盘）。

    与 html_reports 不同：PPT 无 DB 回退列，写失败必须 fail-loud（工具向上报错，用户重试）。"""
    storage.put(artifact_key(report_id), pptx_bytes,
                "application/vnd.openxmlformats-officedocument.presentationml.presentation")


def create_report(db: Session, *, session_id: str | None, title: str, period: str,
                  file_name: str, spec: PptSpec) -> PptReport:
    sid = None
    if session_id:
        try:
            sid = uuid.UUID(str(session_id))
        except (ValueError, TypeError):
            sid = None  # 非 UUID 会话标识（异常场景）容错置空
    row = PptReport(title=title, session_id=sid, period=period, file_name=file_name,
                    spec=spec.model_dump())
    db.add(row)
    db.flush()
    return row


def render_and_store(spec: PptSpec, report_id: str) -> bytes:
    pptx_bytes = render(spec)
    store_artifact(report_id, pptx_bytes)
    return pptx_bytes


def get_owned(db: Session, report_id: str, user_id: str) -> PptReport:
    """读取本人报告：不存在或不归属一律 404（不泄露存在性）。"""
    row = db.get(PptReport, report_id)
    if row is None or row.created_by != user_id:
        raise errors.not_found("报告不存在")
    return row


def read_bytes(row: PptReport) -> bytes:
    return storage.read(artifact_key(str(row.report_id)))
