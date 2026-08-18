"""技能目录与用户启用配置（AI 问数技能改造）。

技能 = 一组工具的命名集合（前端入口 + 目录展示 + 工具过滤）。
- 用户无 user_skills 记录行：不过滤（全量工具，兼容 API/E2E 直连用户）；
- 前端首次拉取目录（GET /skills）懒写入默认 3 核心技能启用行，此后按启用集过滤；
- 模版（skill_templates）按 skill_key 懒播种默认模版，可动态维护。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.models import SkillTemplate, UserSkill

logger = logging.getLogger(__name__)

# 常驻工具：不归属任何技能，始终可用
ALWAYS_TOOLS = {"search_knowledge", "list_companies"}

# 技能目录（静态清单；core=True 为默认启用的 3 核心技能）
SKILL_CATALOG: list[dict] = [
    {
        "skill_key": "post_report", "name": "投后管理报告", "sort_no": 1, "core": True,
        "desc": "选择模版生成投后管理报告，产出 Word 文档（.docx）",
        "tools": ["generate_post_report"], "file_type": "docx",
    },
    {
        "skill_key": "fin_risk_report", "name": "财务风险报告", "sort_no": 2, "core": True,
        "desc": "选择模版生成财务风险报告，产出 PPT 演示文稿（.pptx）",
        "tools": ["generate_fin_risk_report"], "file_type": "pptx",
    },
    {
        "skill_key": "info_fill", "name": "信息填报", "sort_no": 3, "core": True,
        "desc": "创新调查 / 风险填报 / 现金保障试算等信息采集与计算",
        "tools": ["dispatch_risk_fill", "get_risk_fill_status", "start_cash_guarantee_fill"],
    },
    {
        "skill_key": "kpi_fill", "name": "经营者考核", "sort_no": 4, "core": False,
        "desc": "经营者考核指标下发与填报", "tools": ["dispatch_kpi_fill"],
    },
    {
        "skill_key": "ms_feedback", "name": "里程碑反馈", "sort_no": 5, "core": False,
        "desc": "里程碑拆分与进展反馈", "tools": ["dispatch_ms_feedback"],
    },
    {
        "skill_key": "lamp_adjust", "name": "亮灯调整", "sort_no": 6, "core": False,
        "desc": "考核指标亮灯调整", "tools": ["adjust_lamp"],
    },
    {
        "skill_key": "task_stats", "name": "任务执行统计", "sort_no": 7, "core": False,
        "desc": "任务进度与完成率统计", "tools": ["query_task_stats"],
    },
    {
        "skill_key": "generic_dispatch", "name": "通用派发", "sort_no": 8, "core": False,
        "desc": "向指定用户派发通用待办", "tools": ["dispatch_generic_task"],
    },
]

_BY_KEY = {s["skill_key"]: s for s in SKILL_CATALOG}

# 默认模版（懒播种；content 约定：tool=工具调用地址，prompt=触发话术，status=dev 表示开发中）
DEFAULT_TEMPLATES: dict[str, list[dict]] = {
    "post_report": [
        {"category": "投后报告", "name": "标准投后管理报告", "sort_no": 1,
         "content": {"tool": "generate_post_report", "file_type": "docx",
                     "prompt": "生成投后管理报告"}},
    ],
    "fin_risk_report": [
        {"category": "财务风险", "name": "标准财务风险报告", "sort_no": 1,
         "content": {"tool": "generate_fin_risk_report", "file_type": "pptx",
                     "prompt": "生成财务风险报告"}},
    ],
    "info_fill": [
        {"category": "信息填报", "name": "风险填报", "sort_no": 1,
         "content": {"tool": "dispatch_risk_fill", "prompt": "发起风险填报"}},
        {"category": "信息填报", "name": "现金保障试算", "sort_no": 2,
         "content": {"tool": "start_cash_guarantee_fill", "prompt": "发起现金保障试算"}},
        {"category": "信息填报", "name": "创新调查", "sort_no": 3,
         "content": {"status": "dev"}},
    ],
}


def _seed_default_skills(db: Session, user_id: str) -> None:
    """首次接触技能体系：写入默认启用集（3 核心技能开，其余关）。"""
    for s in SKILL_CATALOG:
        db.add(UserSkill(user_id=user_id, skill_key=s["skill_key"], enabled=bool(s["core"])))
    db.flush()
    logger.info("技能默认集懒播种 user=%s 核心技能=%d", user_id, sum(1 for s in SKILL_CATALOG if s["core"]))


def catalog_for_user(db: Session, user_id: str) -> list[dict]:
    """技能目录 + 用户启用态；无记录行时懒播种默认集。"""
    rows = db.scalars(select(UserSkill).where(UserSkill.user_id == user_id)).all()
    if not rows:
        _seed_default_skills(db, user_id)
        rows = db.scalars(select(UserSkill).where(UserSkill.user_id == user_id)).all()
    enabled_map = {r.skill_key: bool(r.enabled) for r in rows}
    return [
        {**{k: v for k, v in s.items() if k != "tools"}, "enabled": enabled_map.get(s["skill_key"], bool(s["core"]))}
        for s in sorted(SKILL_CATALOG, key=lambda x: x["sort_no"])
    ]


def set_enabled(db: Session, user_id: str, skill_key: str, enabled: bool) -> dict:
    """启用/停用技能（upsert）。"""
    if skill_key not in _BY_KEY:
        from ..core import errors

        raise errors.not_found(f"未知技能：{skill_key}")
    row = db.scalars(
        select(UserSkill).where(UserSkill.user_id == user_id, UserSkill.skill_key == skill_key)
    ).first()
    if row is None:
        row = UserSkill(user_id=user_id, skill_key=skill_key, enabled=enabled)
        db.add(row)
    else:
        row.enabled = enabled
    db.flush()
    logger.info("技能启用变更 user=%s skill=%s enabled=%s", user_id, skill_key, enabled)
    return {"skill_key": skill_key, "enabled": enabled}


def tool_allowlist(db: Session, user_id: str) -> set[str] | None:
    """按用户启用技能计算工具白名单；无记录行返回 None（= 不过滤，全量兼容）。"""
    rows = db.scalars(select(UserSkill).where(UserSkill.user_id == user_id)).all()
    if not rows:
        return None
    allow: set[str] = set(ALWAYS_TOOLS)
    for r in rows:
        if not r.enabled:
            continue
        skill = _BY_KEY.get(r.skill_key)
        if skill:
            allow.update(skill["tools"])
    return allow


def templates_for(db: Session, skill_key: str) -> list[dict]:
    """技能模版列表（按 sort_no）；首次访问懒播种默认模版。"""
    if skill_key not in _BY_KEY:
        from ..core import errors

        raise errors.not_found(f"未知技能：{skill_key}")
    rows = db.scalars(
        select(SkillTemplate).where(SkillTemplate.skill_key == skill_key).order_by(SkillTemplate.sort_no)
    ).all()
    if not rows and skill_key in DEFAULT_TEMPLATES:
        for t in DEFAULT_TEMPLATES[skill_key]:
            db.add(SkillTemplate(skill_key=skill_key, category=t["category"], name=t["name"],
                                 sort_no=t["sort_no"], content=t["content"]))
        db.flush()
        logger.info("模版懒播种 skill=%s 数量=%d", skill_key, len(DEFAULT_TEMPLATES[skill_key]))
        rows = db.scalars(
            select(SkillTemplate).where(SkillTemplate.skill_key == skill_key).order_by(SkillTemplate.sort_no)
        ).all()
    return [
        {
            "template_id": r.template_id,
            "skill_key": r.skill_key,
            "category": r.category,
            "name": r.name,
            "sort_no": r.sort_no,
            "content": r.content,
            "enabled": bool(r.enabled),
        }
        for r in rows
    ]


def session_task_type(tool_names: list[str]) -> str:
    """按会话内调用过的工具归类任务类型（历史记录分组用）；未命中归 chat。"""
    for name in tool_names:
        for s in SKILL_CATALOG:
            if name in s["tools"]:
                return s["skill_key"]
    return "chat"
