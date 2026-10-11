"""Xin台配置 · 入口（cockpit_entries）+ 分组卡片（cockpit_groups / cockpit_cards）

配置表非业务数据：无 is_delete 软删（DELETE 为硬删），不走业务审计。
启动时幂等 seed 默认入口与三组卡片（AI工作台 / AI技能 / 业务系统），仅补缺失项、不改已有配置。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import errors
from ...persistence.models import (
    CockpitCard,
    CockpitEntry,
    CockpitGroup,
    SysUser,
)
from ...persistence.session import get_db
from ...services import permissions as perm_svc
from .deps import current_user, require_hq, require_perm
from ..xuanpu_perm import PERM_COCKPIT_MANAGE

router = APIRouter(prefix="/cockpit", tags=["cockpit"])

# 初始入口：XuanPu 三子系统（entry_path 为站内路径，免登 to 白名单 /pro/ /rag/ /mcp/）
SEED_ENTRIES = [
    {"key": "pro", "name": "投后管理", "entry_path": "/pro/", "icon": "", "sort": 10},
    {"key": "rag", "name": "知识中心", "entry_path": "/rag/", "icon": "", "sort": 20},
    {"key": "mcp", "name": "智能协作", "entry_path": "/mcp/", "icon": "", "sort": 30},
]

# 分组卡片 seed：kind=link 页面跳转（link_url 可配）/ task 技能任务卡 / meeting 实时会议卡
# / placeholder 功能占位卡（soon=True 置灰「二期建设中」）
SEED_GROUPS = [
    {"key": "agent", "name": "智能体应用", "sort": 10},
    {"key": "research", "name": "AI 促研", "sort": 20},
    {"key": "tools", "name": "AI 工具", "sort": 30},
    {"key": "system", "name": "业务系统", "sort": 40},
]

SEED_CARDS = [
    # ---- 智能体应用 ----
    {"key": "fin-dashboard", "group_key": "agent", "name": "财务风险看板",
     "subtitle": "财务指标风险分析驾驶舱", "kind": "link", "link_url": "/data/fin_dashboard.html", "sort": 5},
    {"key": "perf-dashboard", "group_key": "agent", "name": "经营者考核看板",
     "subtitle": "经营者考核驾驶舱", "kind": "link", "link_url": "/data/perf_dashboard.html", "sort": 8},
    {"key": "fin-ai", "group_key": "agent", "name": "财务指标深度分析",
     "subtitle": "AI 逐户研判 · 自动生成报告", "kind": "task", "skill_name": "fin-deep-analysis", "sort": 10},
    {"key": "perf-ai", "group_key": "agent", "name": "经营者考核深度分析",
     "subtitle": "AI 全景研判 · 自动生成报告", "kind": "task", "skill_name": "perf-deep-analysis", "sort": 12},
    {"key": "qa", "group_key": "agent", "name": "智能问答",
     "subtitle": "政策解读 · 科研知识问答 · 文献推荐", "kind": "task", "skill_name": "deep_research_v3", "sort": 20},
    {"key": "ontology-graph", "group_key": "agent", "name": "业务图谱",
     "subtitle": "业务本体关系图 · 点击节点展开邻域", "kind": "graph", "sort": 22},
    {"key": "data-apply", "group_key": "agent", "name": "数据申请",
     "subtitle": "数据安全边界 · 数据授权查阅 · 使用申请", "kind": "placeholder", "sort": 25},
    {"key": "ai-prelim", "group_key": "agent", "name": "AI 预审助手",
     "subtitle": "申报指南核对 · 参考文献时效性分析 · 伦理审查提示", "kind": "placeholder", "sort": 28},
    {"key": "talent-mgmt", "group_key": "agent", "name": "人才管理助手",
     "subtitle": "项目进度辅助 · 智能选人 · 人才发现", "kind": "placeholder", "soon": True, "sort": 30},
    {"key": "fill", "group_key": "agent", "name": "信息填报",
     "subtitle": "风险填报 · 资金保障 · 指标考核", "kind": "task", "skill_name": "信息填报", "sort": 40},
    {"key": "risk-report", "group_key": "agent", "name": "财务风险报告",
     "subtitle": "财务风险 AI 报告生成", "kind": "task", "skill_name": "财务风险报告", "sort": 42},
    {"key": "post-invest-report", "group_key": "agent", "name": "投后管理报告",
     "subtitle": "投后管理 AI 报告生成", "kind": "task", "skill_name": "投后管理报告", "sort": 44},
    {"key": "fixed-income", "group_key": "agent", "name": "固收Skill",
     "subtitle": "固收投研分析", "kind": "task", "skill_name": "固收Skill", "sort": 46},
    {"key": "docgen", "group_key": "agent", "name": "文档生成",
     "subtitle": "公文与报告文档生成",
     "kind": "link",
     "link_url": "http://192.168.1.161:8003/workbench?redirect=http://192.168.1.161:9096/login", "sort": 48},
    # ---- AI 促研 ----
    {"key": "proj-apply", "group_key": "research", "name": "项目申报",
     "subtitle": "智能匹配 · 材料AI质控 · 时间节点控制", "kind": "placeholder", "sort": 10},
    {"key": "proj-process", "group_key": "research", "name": "过程管理",
     "subtitle": "进度填报 · 中期检查 · 变更与终止办理", "kind": "placeholder", "sort": 20},
    {"key": "proj-close", "group_key": "research", "name": "结题验收",
     "subtitle": "任务书核对 · 阶段目标比对 · 指标完成检查", "kind": "placeholder", "sort": 30},
    {"key": "proj-output", "group_key": "research", "name": "科研成果",
     "subtitle": "内外部成果统一登记 · 期刊分类识别 · 统计导出", "kind": "placeholder", "sort": 40},
    {"key": "talent-profile", "group_key": "research", "name": "人才档案",
     "subtitle": "综合科研/医疗/教学评价 · 跨学科团队匹配", "kind": "placeholder", "sort": 50},
    # ---- AI 工具 ----
    {"key": "minutes", "group_key": "tools", "name": "实时纪要",
     "subtitle": "实时转写 · 自动生成纪要 · 会议录音管理", "kind": "meeting", "sort": 10},
    {"key": "lit-assistant", "group_key": "tools", "name": "文献助手",
     "subtitle": "文献检索 · 快速阅读 · 多篇比较 · 问答", "kind": "placeholder", "soon": True, "sort": 20},
    {"key": "med-trans", "group_key": "tools", "name": "医学翻译",
     "subtitle": "中文摘要/文章 · 医学专业英文翻译", "kind": "placeholder", "soon": True, "sort": 30},
    {"key": "stat-guide", "group_key": "tools", "name": "统计指导",
     "subtitle": "统计方法选择 · 样本量计算 · 软件操作指导", "kind": "placeholder", "soon": True, "sort": 40},
    # ---- 业务系统 ----
    {"key": "med-platform", "group_key": "system", "name": "医疗数据中台",
     "subtitle": "区外账号隔离 · 医疗数据对接边界控制", "kind": "placeholder", "sort": 10},
    {"key": "pm-system", "group_key": "system", "name": "项目管理系统",
     "subtitle": "项目过程管理 · 进度跟踪", "kind": "placeholder", "sort": 20},
    {"key": "oa", "group_key": "system", "name": "OA 办公系统",
     "subtitle": "公文流转 · 审批签章对接", "kind": "placeholder", "sort": 30},
    {"key": "ext-research", "group_key": "system", "name": "外部科研平台",
     "subtitle": "市级/国家级/学协会课题立项登记入口", "kind": "placeholder", "sort": 40},
    {"key": "ops", "group_key": "system", "name": "运营管理系统",
     "subtitle": "年度考核 · 运营管理",
     "kind": "link", "link_url": "http://192.168.1.161:9096/dashboard/AnnualEvaluation/index", "sort": 50},
    {"key": "qingshan-kb", "group_key": "system", "name": "青山知识库",
     "subtitle": "知识中心文档与知识库", "kind": "link", "link_url": "/rag/", "sort": 52},
    {"key": "tide-cowork", "group_key": "system", "name": "TIDE CoWork",
     "subtitle": "智能协作工作台", "kind": "link", "link_url": "/mcp/", "sort": 54},
    {"key": "reserved-slot", "group_key": "system", "name": "敬请期待",
     "subtitle": "", "kind": "placeholder", "sort": 90},
]


def seed_entries(db: Session) -> int:
    """幂等 seed：仅补缺失 key。返回新增条数（调用方负责 commit）。"""
    existing = set(db.scalars(select(CockpitEntry.key)).all())
    added = 0
    for item in SEED_ENTRIES:
        if item["key"] in existing:
            continue
        db.add(CockpitEntry(**item))
        added += 1
    if added:
        db.flush()
    return added


def seed_cards(db: Session) -> int:
    """幂等 seed 分组 + 卡片：仅补缺失 key。返回新增条数（调用方负责 commit）。"""
    added = 0
    groups = set(db.scalars(select(CockpitGroup.key)).all())
    for item in SEED_GROUPS:
        if item["key"] in groups:
            continue
        db.add(CockpitGroup(**item))
        added += 1
    cards = set(db.scalars(select(CockpitCard.key)).all())
    for item in SEED_CARDS:
        if item["key"] in cards:
            continue
        db.add(CockpitCard(**item))
        added += 1
    if added:
        db.flush()
    return added


def _view(e: CockpitEntry) -> dict:
    return {
        "id": e.id,
        "key": e.key,
        "name": e.name,
        "entry_path": e.entry_path,
        "icon": e.icon,
        "sort": e.sort,
        "enabled": e.enabled,
    }


@router.get("/entries")
def list_entries(user: SysUser = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(CockpitEntry).order_by(CockpitEntry.sort, CockpitEntry.id)).all()
    return {"items": [_view(r) for r in rows]}


class EntryIn(BaseModel):
    key: str
    name: str
    entry_path: str
    icon: str = ""
    sort: int = 0
    enabled: bool = True


class EntryPatch(BaseModel):
    name: str | None = None
    entry_path: str | None = None
    icon: str | None = None
    sort: int | None = None
    enabled: bool | None = None


@router.post("/entries", status_code=201)
def create_entry(body: EntryIn, user: SysUser = Depends(require_hq),
                 db: Session = Depends(get_db)):
    if not body.entry_path.startswith("/"):
        raise errors.validation("入口路径须以 / 开头（如 /pro/）")
    if db.scalars(select(CockpitEntry).where(CockpitEntry.key == body.key)).first():
        raise errors.validation("入口标识已存在")
    entry = CockpitEntry(
        key=body.key, name=body.name, entry_path=body.entry_path,
        icon=body.icon, sort=body.sort, enabled=body.enabled,
    )
    db.add(entry)
    db.flush()
    return _view(entry)


@router.patch("/entries/{entry_id}")
def update_entry(entry_id: int, body: EntryPatch, user: SysUser = Depends(require_hq),
                 db: Session = Depends(get_db)):
    entry = db.get(CockpitEntry, entry_id)
    if entry is None:
        raise errors.not_found("入口不存在")
    if body.entry_path is not None and not body.entry_path.startswith("/"):
        raise errors.validation("入口路径须以 / 开头（如 /pro/）")
    for field in ("name", "entry_path", "icon", "sort", "enabled"):
        val = getattr(body, field)
        if val is not None:
            setattr(entry, field, val)
    db.flush()
    return _view(entry)


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: int, user: SysUser = Depends(require_hq),
                 db: Session = Depends(get_db)):
    entry = db.get(CockpitEntry, entry_id)
    if entry is None:
        raise errors.not_found("入口不存在")
    db.delete(entry)  # 配置表：硬删
    db.flush()
    return {"ok": True}


# ---------- 分组卡片（Xin台主界面） ----------


def _card_view(c: CockpitCard) -> dict:
    return {
        "id": c.id,
        "key": c.key,
        "group_key": c.group_key,
        "name": c.name,
        "subtitle": c.subtitle or "",
        "kind": c.kind,  # link / task / meeting / placeholder
        "link_url": c.link_url,
        "skill_id": c.skill_id,
        "skill_name": c.skill_name,
        "icon": c.icon,
        "soon": bool(c.soon),
        "sort": c.sort,
        "enabled": c.enabled,
    }


SURFACES = ("dashboard", "xin_tai")


def _scms_card_view(c: CockpitCard) -> dict:
    """scms 侧 action/params 渲染体系视图（驾驶舱 surface 专用）。"""
    return {
        "id": c.id, "key": c.key, "group_key": c.group_key, "name": c.name,
        "description": c.description, "pending": bool(c.pending),
        "action": c.action, "params": c.params or {}, "icon": c.icon, "sort": c.sort,
    }


@router.get("/cards")
def list_cards(surface: str = "xin_tai", user: SysUser = Depends(current_user),
               db: Session = Depends(get_db)):
    """分组卡片（启用组 + 启用卡，组间/组内均按 sort 升序）。

    - surface=dashboard：scms 驾驶舱体系（action/params 渲染；perm_key 卡批量判权，
      未接入放行、判权失败 fail-closed 隐藏）
    - surface=xin_tai（默认）：Xin台体系（kind/link_url/soon 渲染）
    """
    if surface not in SURFACES:
        from ...core import errors
        raise errors.validation(f"surface 须为 {'/'.join(SURFACES)}")
    groups = db.scalars(
        select(CockpitGroup).where(
            CockpitGroup.surface == surface, CockpitGroup.enabled.is_(True)
        ).order_by(CockpitGroup.sort, CockpitGroup.id)
    ).all()
    cards = db.scalars(
        select(CockpitCard).where(
            CockpitCard.enabled.is_(True),
            CockpitCard.group_key.in_([g.key for g in groups]) if groups else False,
        ).order_by(CockpitCard.sort, CockpitCard.id)
    ).all()
    if surface == "dashboard":
        allowed = perm_svc.visible_perm_keys(user.user_id, [c.perm_key for c in cards if c.perm_key])
        by_group: dict[str, list[dict]] = {}
        for c in cards:
            if c.perm_key and c.perm_key not in allowed:
                continue
            by_group.setdefault(c.group_key, []).append(_scms_card_view(c))
        view_fn = _scms_card_view
    else:
        by_group = {}
        for c in cards:
            by_group.setdefault(c.group_key, []).append(_card_view(c))
        view_fn = _card_view
    return {
        "items": [
            {"key": g.key, "name": g.name, "sort": g.sort, "cards": by_group.get(g.key, [])}
            for g in groups
        ]
    }


def _admin_card_view(c: CockpitCard) -> dict:
    return {
        "id": c.id, "key": c.key, "group_key": c.group_key, "name": c.name,
        "description": c.description, "pending": bool(c.pending),
        "action": c.action, "params": c.params or {}, "icon": c.icon or "",
        "sort": c.sort, "enabled": bool(c.enabled), "perm_key": c.perm_key,
    }


@router.get("/admin/config")
def admin_config(user: SysUser = Depends(require_hq), db: Session = Depends(get_db)):
    """管理端全量配置（含禁用分组/卡片、不做权限过滤），驾驶舱配置页专用。"""
    groups = db.scalars(
        select(CockpitGroup).order_by(CockpitGroup.surface, CockpitGroup.sort, CockpitGroup.id)
    ).all()
    cards = db.scalars(select(CockpitCard).order_by(CockpitCard.sort, CockpitCard.id)).all()
    by_group: dict[str, list[dict]] = {}
    for c in cards:
        by_group.setdefault(c.group_key, []).append(_admin_card_view(c))
    return {
        "groups": [
            {
                "id": g.id, "key": g.key, "surface": g.surface, "name": g.name,
                "sort": g.sort, "enabled": bool(g.enabled),
                "cards": by_group.get(g.key, []),
            }
            for g in groups
        ]
    }


class GroupIn(BaseModel):
    key: str
    name: str
    surface: str = "xin_tai"
    sort: int = 0
    enabled: bool = True


class GroupPatch(BaseModel):
    name: str | None = None
    surface: str | None = None
    sort: int | None = None
    enabled: bool | None = None


@router.post("/groups", status_code=201)
def create_group(body: GroupIn, user: SysUser = Depends(require_hq),
                 db: Session = Depends(get_db)):
    if db.scalars(select(CockpitGroup).where(CockpitGroup.key == body.key)).first():
        raise errors.validation("分组标识已存在")
    group = CockpitGroup(key=body.key, name=body.name, surface=body.surface, sort=body.sort, enabled=body.enabled)
    db.add(group)
    db.flush()
    return {"id": group.id, "key": group.key, "name": group.name,
            "sort": group.sort, "enabled": group.enabled}


@router.patch("/groups/{group_id}")
def update_group(group_id: int, body: GroupPatch, user: SysUser = Depends(require_hq),
                 db: Session = Depends(get_db)):
    group = db.get(CockpitGroup, group_id)
    if group is None:
        raise errors.not_found("分组不存在")
    for field in ("name", "surface", "sort", "enabled"):
        val = getattr(body, field)
        if val is not None:
            setattr(group, field, val)
    db.flush()
    return {"id": group.id, "key": group.key, "name": group.name,
            "sort": group.sort, "enabled": group.enabled}


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, user: SysUser = Depends(require_hq),
                 db: Session = Depends(get_db)):
    group = db.get(CockpitGroup, group_id)
    if group is None:
        raise errors.not_found("分组不存在")
    db.delete(group)  # 配置表：硬删（组内卡片保留，可改挂其他分组）
    db.flush()
    return {"ok": True}


class CardIn(BaseModel):
    key: str
    group_key: str
    name: str
    subtitle: str = ""
    kind: str = "task"  # link / task / meeting / placeholder
    link_url: str | None = None
    skill_id: int | None = None
    skill_name: str | None = None
    icon: str = ""
    soon: bool = False
    sort: int = 0
    enabled: bool = True


class CardPatch(BaseModel):
    group_key: str | None = None
    name: str | None = None
    subtitle: str | None = None
    kind: str | None = None
    link_url: str | None = None
    skill_id: int | None = None
    skill_name: str | None = None
    icon: str | None = None
    soon: bool | None = None
    sort: int | None = None
    enabled: bool | None = None


def _check_card(body: CardIn | CardPatch) -> None:
    if body.kind is not None and body.kind not in ("link", "task", "meeting", "placeholder"):
        raise errors.validation("卡片类型须为 link / task / meeting / placeholder")
    if body.kind == "link" and not (body.link_url or "").strip():
        raise errors.validation("跳转卡片须配置跳转地址")


@router.post("/cards", status_code=201)
def create_card(body: CardIn, user: SysUser = Depends(require_perm(PERM_COCKPIT_MANAGE)),
                db: Session = Depends(get_db)):
    _check_card(body)
    if db.scalars(select(CockpitCard).where(CockpitCard.key == body.key)).first():
        raise errors.validation("卡片标识已存在")
    card = CockpitCard(
        key=body.key, group_key=body.group_key, name=body.name, subtitle=body.subtitle,
        kind=body.kind, link_url=body.link_url, skill_id=body.skill_id, skill_name=body.skill_name,
        icon=body.icon, soon=body.soon, sort=body.sort, enabled=body.enabled,
    )
    db.add(card)
    db.flush()
    return _card_view(card)


@router.patch("/cards/{card_id}")
def update_card(card_id: int, body: CardPatch,
                user: SysUser = Depends(require_perm(PERM_COCKPIT_MANAGE)),
                db: Session = Depends(get_db)):
    card = db.get(CockpitCard, card_id)
    if card is None:
        raise errors.not_found("卡片不存在")
    _check_card(body)
    kind = body.kind or card.kind
    if kind == "link" and not (body.link_url or card.link_url or "").strip():
        raise errors.validation("跳转卡片须配置跳转地址")
    for field in ("group_key", "name", "subtitle", "kind", "link_url", "skill_id",
                  "skill_name", "icon", "soon", "sort", "enabled"):
        val = getattr(body, field)
        if val is not None:
            setattr(card, field, val)
    db.flush()
    return _card_view(card)


@router.delete("/cards/{card_id}")
def delete_card(card_id: int, user: SysUser = Depends(require_perm(PERM_COCKPIT_MANAGE)),
                db: Session = Depends(get_db)):
    card = db.get(CockpitCard, card_id)
    if card is None:
        raise errors.not_found("卡片不存在")
    db.delete(card)  # 配置表：硬删
    db.flush()
    return {"ok": True}


# ---------------- 驾驶舱（dashboard surface）seed（scms 移植，幂等） ----------------


SEED_DASHBOARD_GROUPS = [
    {"key": "dashboard_scene", "surface": "dashboard", "name": "应用场景", "sort": 10},
    {"key": "dashboard_kb", "surface": "dashboard", "name": "知识库", "sort": 20},
    {"key": "dashboard_tool", "surface": "dashboard", "name": "AI工具", "sort": 30},
    {"key": "dashboard_system", "surface": "dashboard", "name": "系统", "sort": 90},
]
SEED_DASHBOARD_CARDS = [
    # 应用场景：报告技能引导进对话 / 简报弹窗 / 占位
    {"key": "scene_fin_risk_report", "group_key": "dashboard_scene", "name": "风险预警财务指标报告",
     "action": "skill_prompt", "params": {"skill": "fin_risk_report"}, "description": "生成 PPT 演示", "sort": 10},
    {"key": "scene_post_report", "group_key": "dashboard_scene", "name": "投后管理报告",
     "action": "skill_prompt", "params": {"skill": "post_report"}, "description": "生成 Word 报告", "sort": 20},
    {"key": "scene_brief", "group_key": "dashboard_scene", "name": "信息简报",
     "action": "modal", "params": {"modal": "brief"}, "description": "月度企业简报", "sort": 30},
    # 技能执行卡：跳转对话页预选技能（⚡徽标），用户补充输入后发送执行
    {"key": "scene_research", "group_key": "dashboard_scene", "name": "行业研究报告",
     "action": "skills", "params": {"skill": "deep_research_v3_with_pdf"}, "sort": 40},
    {"key": "scene_fixed_income", "group_key": "dashboard_scene", "name": "固收管理",
     "action": "skills", "params": {"skill": "fixed-income-advisor"}, "description": "固收理财推荐", "sort": 50},
    # 知识库
    {"key": "kb_qingshan", "group_key": "dashboard_kb", "name": "青山知识库",
     "action": "sso_kb", "sort": 10},
    # AI工具：会议生成=站内功能组件（前端渲染 MeetingToolCard）；报告中心入口（基址仍走 settings）
    {"key": "tool_meeting", "group_key": "dashboard_tool", "name": "会议生成",
     "action": "feature", "params": {"feature": "meeting"}, "description": "实时转写 · 自动生成纪要", "sort": 10},
    {"key": "tool_report_center", "group_key": "dashboard_tool", "name": "报告中心",
     "action": "feature", "params": {"feature": "report_center"}, "sort": 20},
    # 系统：SCMS 出票卡（原 config.py COCKPIT_BIZ_CARDS 迁移）
    {"key": "dash_risk", "group_key": "dashboard_system", "name": "风险预警财务指标",
     "action": "sso_scms", "params": {"redirect": "/finance-warning/finance-statistics-sample"}, "sort": 10},
    {"key": "dash_kpi", "group_key": "dashboard_system", "name": "经营考核管理",
     "action": "sso_scms", "params": {"redirect": "/management/indicator-statistics"}, "sort": 20},
    {"key": "dash_kpi_dept", "group_key": "dashboard_system", "name": "本部部门考核",
     "action": "sso_scms", "params": {"redirect": "/dept/annual"}, "sort": 30},
    {"key": "dash_post", "group_key": "dashboard_system", "name": "投后管理",
     "action": "sso_scms", "params": {"redirect": "/enterprise/company"}, "sort": 40},
    # 管理入口：驾驶舱卡片配置页（前端路由 /config/cockpit，页面内再受 require_hq 约束）
    {"key": "dashboard_config", "group_key": "dashboard_system", "name": "驾驶舱配置",
     "action": "feature", "params": {"feature": "cockpit_config"}, "description": "卡片分组与入口维护", "sort": 50},
]


# 存量卡定向升级：seed 定义演进时，库中仍为历史 seed 原值的卡迁移到新定义（用户自定义过则不动）
LEGACY_CARD_UPGRADES = {
    # 行业研究报告：占位卡（skill_prompt/research + pending）→ 技能执行卡（跳对话页预选技能）
    "scene_research": {"action": "skill_prompt", "params": {"skill": "research"}, "pending": True},
}


def seed_dashboard(db: Session) -> int:
    """幂等 seed：补缺失 key；存量卡若与历史 seed 原值一致则按 LEGACY_CARD_UPGRADES 定向升级。
    返回新增条数（调用方负责 commit）。"""
    added = 0
    existing_groups = set(db.scalars(select(CockpitGroup.key)).all())
    for item in SEED_DASHBOARD_GROUPS:
        if item["key"] in existing_groups:
            continue
        db.add(CockpitGroup(**item))
        added += 1
    rows = db.scalars(
        select(CockpitCard).where(CockpitCard.key.in_([c["key"] for c in SEED_DASHBOARD_CARDS]))
    ).all()
    by_key = {r.key: r for r in rows}
    for item in SEED_DASHBOARD_CARDS:
        row = by_key.get(item["key"])
        if row is None:
            db.add(CockpitCard(**item))
            added += 1
            continue
        legacy = LEGACY_CARD_UPGRADES.get(item["key"])
        if not legacy:
            continue
        if (row.action == legacy["action"] and (row.params or {}) == legacy["params"]
                and bool(row.pending) == bool(legacy.get("pending"))):
            row.action = item["action"]
            row.params = item.get("params")
            row.pending = bool(item.get("pending", False))
    db.flush()
    return added


