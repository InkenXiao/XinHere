"""scms 移植工具集：任务内核 13 工具 + SCMS 问数数据工具（服务账号模式）。

build_task_tools 与本仓 tools.build_common_tools（玄圃 29 工具）无重名；
build_data_tools 依赖 SCMS_NL2SQL_URL/SCMS_QUERY_DATA_URL（未配置返回空表，零开销）。
"""
from __future__ import annotations

import json
import re
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from typing import Annotated

from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import interrupt

from ...core.config import settings
from ...services import identity as identity_svc
from ...services import html_reports as html_report_svc
from ...services import todo as todo_svc
from ..scms import token_auth
from .tool_base import ToolAnswerStream, ToolCtx, new_component_id, tool_scope

logger = logging.getLogger(__name__)


def _interrupt_card(ctx: ToolCtx, kind: str, props: dict) -> dict:
    """组件再确认卡的标准 interrupt payload（resume 前只做只读准备，重放安全）。"""
    return {
        "component_id": new_component_id(kind),
        "kind": kind,
        "kind_version": 1,
        "props": props,
        "interrupt_id": new_component_id("intr"),
        "version": 1,
    }


def _resolve_dispatcher_task(db, user_id: str, task_text: str):
    """派发人 free-text 任务定位：全匹配 id > 标题唯一命中 > 多义返回列表（None, 提示）。"""
    from sqlalchemy import select

    from ...persistence.models import BizTask

    q = (task_text or "").strip()
    stmt = (
        select(BizTask)
        .where(BizTask.dispatcher_id == user_id, BizTask.status == "open")
        .order_by(BizTask.created_at.desc())
    )
    tasks = db.scalars(stmt.limit(20)).all()
    if not tasks:
        return None, "你当前没有进行中的派发任务。"
    exact = next((t for t in tasks if t.task_id == q), None)
    if exact:
        return exact, ""
    hits = [t for t in tasks if q and (q in t.title or q in t.task_id)]
    if len(hits) == 1:
        return hits[0], ""
    listing = "；".join(f"「{t.title}」（{t.task_id}）" for t in tasks[:8])
    if len(hits) > 1:
        listing = "；".join(f"「{t.title}」（{t.task_id}）" for t in hits[:8])
    return None, f"匹配到多个派发中任务，请说明具体哪一个：{listing}"


def do_dispatch(db, *, user_id: str, session_id: str, title: str, assignees: list[dict],
                due=None, review_required: bool = False, note: str = "",
                template_key: str | None = None, anchor: dict | None = None,
                parent_task_id: str | None = None, period: str | None = None) -> str:
    """AI 派发统一落库（工具 L0 免卡与 arrange-confirm 卡处理器共用）：
    template_key 非空走模板小流程 spawn_task，否则建通用任务。返回给 LLM 的 summary。
    template_key 支持 dyn_ 前缀动态填报模板（Excel 上传生成，定义在 dynamic_template 表）。"""
    ids = [str(a["user_id"]) for a in assignees]
    names = "、".join(a.get("name") or i for a, i in zip(assignees, ids))
    if template_key:
        # AI 常直接传用户口中的模板名称 → 统一按 key/名称解析成真实 template_key
        template_key, tpl_name = _resolve_template_key(db, template_key)
        r = todo_svc.spawn_task(
            db, template_key=template_key, dispatcher_id=user_id, assignee_ids=ids,
            period=period, anchor=anchor, parent_task_id=parent_task_id, due=due,
            trigger="ai",
        )
        logger.info("AI 模板派发 sid=%s task=%s tpl=%s 人数=%d",
                    session_id, r["task_id"], template_key, len(ids))
        return f"已按模板「{tpl_name}」派发「{r['title']}」给 {names}"
    task = todo_svc.create_task(
        db, scene="generic", title=title, dispatcher_id=user_id,
        payload={"content": note}, review_required=review_required, due=due,
    )
    for a in assignees:
        todo_svc.create_todo(
            db, task=task, assignee_id=str(a["user_id"]), kind="action",
            title=title, sub=note or "来自 AI 安排", ref={"source": "ai_arrange", "content": note},
            due=due, session_id=session_id or None,
        )
    logger.info("AI 通用派发 sid=%s task=%s 人数=%d 审核=%s",
                session_id, task.task_id, len(ids), review_required)
    return f"已安排待办「{title}」给 {names}"


def _template_options(db) -> list[dict]:
    """派发卡「任务类型」下拉数据源：静态小流程 + 动态填报模板（dyn_ 前缀）。
    OA 审批模板（scene=oa：请假申请/费用报销/通用审批单）走 oa_apply 表单卡流程，不进此下拉。"""
    from ...modules.dynamic_template import services as dyn_tpl_svc
    from ...services import task_templates

    static = [t for t in task_templates.list_template_views() if t.get("scene") != "oa"]
    return dyn_tpl_svc.merge_dynamic_into_list(db, static)


def _resolve_template_key(db, key_or_name: str) -> tuple[str, str]:
    """template_key 支持传 key 或模板名称（AI 常直接传用户说的名称）：
    dyn_ 前缀按 ID 精确 → 静态 key 精确 → 名称精确 → 唯一子串匹配。
    返回 (template_key, name)；匹配不到或多义时报可用模板清单。"""
    from ...core import errors
    from ...services import task_templates

    s = (key_or_name or "").strip()
    if not s:
        raise errors.validation("模板不能为空")
    if s.startswith("dyn_"):
        from ...modules.dynamic_template import services as dyn_tpl_svc

        return s, dyn_tpl_svc.get_template_row(db, s[4:]).name
    try:
        return s, task_templates.get_template(s)["name"]
    except Exception:
        pass
    opts = _template_options(db)
    for t in opts:
        if t["name"] == s:
            return t["template_key"], t["name"]
    hits = [t for t in opts if s in t["name"] or t["name"] in s]
    if len(hits) == 1:
        return hits[0]["template_key"], hits[0]["name"]
    names = "、".join(t["name"] for t in opts)
    if len(hits) > 1:
        raise errors.validation(f"「{s}」匹配到多个模板，请说完整名称：{'、'.join(t['name'] for t in hits)}")
    raise errors.not_found(f"未匹配到模板「{s}」（可用模板：{names or '无'}）")


def build_task_tools(ctx: ToolCtx) -> list:
    """任务内核工具：通用 AI 待办（需求B）安排/处理/查询四件套。
    （list_companies/dispatch_generic_task/query_task_stats 按业务收敛要求移除，git 历史可恢复。）"""

    @tool(
        "arrange_task",
        description=(
            "在对话中安排一项通用待办（需求B），弹出两步卡（第一步卡上填 待办描述/截止时间/处理人，"
            "第二步确认）。title=待办描述（从用户消息提取，可空）；assignees=处理人名称列表"
            "（来自用户 @ 提及或明确指定，可空）；due=截止日期 YYYY-MM-DD（可空，"
            "用户给的 20260910/9月10日 等写法归一化后传入）；note=补充说明（可选）；"
            "review_required=完成后是否需派发人审核（默认 false）。"
            "用户表达安排/派发意图时立即调用本工具出卡（参数不全也出卡，由用户在卡上补齐），"
            "不要用文字追问参数；只有用户消息完全无法理解为安排待办时才询问。"
            "卡上第一步可选择任务类型（普通待办 / 模板任务，含 Excel 上传生成的动态填报模板），"
            "用户说「派发填报任务」但没给模板名时不要用文字追问模板，直接出卡让用户在卡上选。"
        ),
    )
    def arrange_task(
        title: str = "",
        assignees: list[str] | None = None,
        due: str | None = None,
        note: str | None = None,
        review_required: bool = False,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"title": title, "assignees": assignees, "due": due, "note": note,
                "review_required": review_required}
        with tool_scope(ctx, "arrange_task", tool_call_id, args) as db:
            # 两步卡：参数在卡第一步补齐，此处只把已给的处理人解析为预填（解析不到留空不阻断）
            resolved = identity_svc.resolve_labels_to_users(assignees or [])
            payload = _interrupt_card(ctx, "arrange-confirm", {
                "title": title or "",
                "note": note or "",
                "due": due or "",
                "review_required": review_required,
                "assignees": resolved["assignees"],
                "unknown": resolved["unknown"],
                "editable": True,
                "templates": _template_options(db),
            })
            rv = interrupt(payload)  # resume 后从此返回；解析为只读，重放安全
        if rv.get("action") == "cancelled":
            return "用户取消了本次待办安排。如需调整（如更换处理人、改标题），请在用户补充后重新调用。"
        return rv.get("summary", f"已安排待办「{title or '待办'}」")

    @tool(
        "list_my_todos",
        description="列出当前用户进行中的待办（标题+todo_id+状态）。安排待办后告知对方、或处理人要提交完成/不涉及前，先用本工具拿到 todo_id。",
    )
    def list_my_todos(tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "list_my_todos", tool_call_id, {}) as db:
            from types import SimpleNamespace

            items = todo_svc.list_todos(db, SimpleNamespace(user_id=ctx.user_id), "assignee")
        live = [t for t in items if t["status"] in ("pending", "returned", "submitted", "na_pending")]
        if not live:
            return "当前没有进行中的待办。"
        zh = {"pending": "待反馈", "returned": "已退回", "submitted": "待审批", "na_pending": "不涉及待确认"}
        return "；".join(f"{t['title']}（{t['todo_id']}，{zh.get(t['status'], t['status'])}）" for t in live[:10])

    @tool(
        "request_todo_done",
        description="处理人在对话中提交某待办的完成说明（弹出完成卡：完成说明+交付件上传，需审核的任务提交后转待审批）。todo_id 用 list_my_todos 查询。",
    )
    def request_todo_done(
        todo_id: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "request_todo_done", tool_call_id, {"todo_id": todo_id}) as db:
            from ...persistence.models import BizTask, BizTodo

            t = db.get(BizTodo, todo_id)
            if t is None:
                return "待办不存在"
            if t.assignee_id != ctx.user_id:
                return "仅待办处理人本人可提交完成"
            if t.status not in ("pending", "returned"):
                return f"待办当前状态（{t.status}）不允许提交完成"
            task = db.get(BizTask, t.task_id)
            review_required = bool(task.review_required) if task else False
            title = t.title
        payload = _interrupt_card(ctx, "todo-done-card", {
            "todo_id": todo_id, "title": title, "review_required": bool(review_required),
        })
        rv = interrupt(payload)
        if rv.get("action") == "cancelled":
            return "用户取消了本次完成提交"
        return rv.get("summary", "完成提交成功")

    @tool(
        "request_todo_na",
        description="处理人在对话中对某待办发起不涉及申请（弹出说明卡：填写不涉及原因，提交后待派发人确认）。todo_id 用 list_my_todos 查询。",
    )
    def request_todo_na(
        todo_id: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "request_todo_na", tool_call_id, {"todo_id": todo_id}) as db:
            from ...persistence.models import BizTodo

            t = db.get(BizTodo, todo_id)
            if t is None:
                return "待办不存在"
            if t.assignee_id != ctx.user_id:
                return "仅待办处理人本人可发起不涉及申请"
            if t.status not in ("pending", "returned"):
                return f"待办当前状态（{t.status}）不允许发起不涉及"
            title = t.title
        payload = _interrupt_card(ctx, "todo-na-card", {"todo_id": todo_id, "title": title})
        rv = interrupt(payload)
        if rv.get("action") == "cancelled":
            return "用户取消了本次不涉及申请"
        return rv.get("summary", "不涉及申请已提交")

    @tool(
        "review_submissions",
        description="派发人逐条审核处理人提交的待办（弹出审核卡：逐条 确认通过/退回需填说明/跳过）。适用于集中处理「待审批」队列。",
    )
    def review_submissions(tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "review_submissions", tool_call_id, {}) as db:
            items = todo_svc.dispatch_queue(db, ctx.user_id, ["submitted"])
            slim = [
                {k: it.get(k) for k in ("todo_id", "title", "assignee_name", "completion_text",
                                        "submitted_at", "task_title")}
                for it in items
            ]
        if not slim:
            return "当前没有待审核的提交。"
        rv = interrupt(_interrupt_card(ctx, "todo-review-card", {"items": slim}))
        if rv.get("action") == "cancelled":
            return "已取消本次审核"
        return rv.get("summary", "审核完成")

    @tool(
        "confirm_na_requests",
        description="派发人逐条确认处理人的「不涉及」申请（弹出确认卡：逐条 确认不涉及/驳回再次派发需填说明/跳过）。",
    )
    def confirm_na_requests(tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "confirm_na_requests", tool_call_id, {}) as db:
            items = todo_svc.dispatch_queue(db, ctx.user_id, ["na_pending"])
            slim = [
                {k: it.get(k) for k in ("todo_id", "title", "assignee_name", "na_reason",
                                        "na_at", "task_title")}
                for it in items
            ]
        if not slim:
            return "当前没有待确认的不涉及申请。"
        rv = interrupt(_interrupt_card(ctx, "todo-na-review-card", {"items": slim}))
        if rv.get("action") == "cancelled":
            return "已取消本次确认"
        return rv.get("summary", "确认完成")

    @tool(
        "remind_pending",
        description="派发人催办（弹出催办卡：逐条勾选已下发多日未反馈的待办并下发提醒）。",
    )
    def remind_pending(tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "remind_pending", tool_call_id, {}) as db:
            items = todo_svc.dispatch_queue(db, ctx.user_id, ["pending", "returned"])
            slim = [
                {k: it[k] for k in ("todo_id", "title", "assignee_name", "created_at",
                                    "remind_count", "task_title")}
                for it in items
            ]
        if not slim:
            return "当前没有需要催办的待办（全部已提交）。"
        rv = interrupt(_interrupt_card(ctx, "todo-remind-card", {"items": slim}))
        if rv.get("action") == "cancelled":
            return "已取消本次催办"
        return rv.get("summary", "催办完成")

    @tool(
        "append_task_assignees",
        description="给派发中的任务追加承办人（弹出追加卡：@ 搜索选择人员，重复者单独列出不再派发）。task=任务标题或 task_id。",
    )
    def append_task_assignees(
        task: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "append_task_assignees", tool_call_id, {"task": task}) as db:
            found, hint = _resolve_dispatcher_task(db, ctx.user_id, task)
            if found is None:
                return hint
            from sqlalchemy import select

            from ...persistence.models import BizTodo

            existing_ids = db.scalars(
                select(BizTodo.assignee_id).where(BizTodo.task_id == found.task_id)
            ).all()
            names = identity_svc.user_names(sorted(set(existing_ids)))
        rv = interrupt(_interrupt_card(ctx, "todo-append-card", {
            "task_id": found.task_id, "task_title": found.title,
            "existing": [{"user_id": k, "name": v} for k, v in names.items()],
        }))
        if rv.get("action") == "cancelled":
            return "已取消追加"
        return rv.get("summary", "追加完成")

    @tool(
        "end_task_flow",
        description="结束一个派发任务（弹出结束卡：状态统计+未完成清单警告，全部完成可直接结束，有未完成需勾选强制结束）。task=任务标题或 task_id。",
    )
    def end_task_flow(
        task: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "end_task_flow", tool_call_id, {"task": task}) as db:
            found, hint = _resolve_dispatcher_task(db, ctx.user_id, task)
            if found is None:
                return hint
            board = todo_svc.board(db, found.task_id, ctx.user_id)
        unfinished = [
            {k: t[k] for k in ("todo_id", "title", "assignee_name", "status")}
            for g in board["groups"].values() for t in g
            if t["status"] in ("pending", "returned", "submitted", "na_pending")
        ]
        stats = {s: len(v) for s, v in board["groups"].items()}
        total = sum(stats.values())
        done = stats.get("completed", 0) + stats.get("na_closed", 0)
        if total > 0 and done == total:
            return f"任务「{board['title']}」全部 {total} 项均已完成/关闭，可直接结束。请确认后调用并明确结束。"
        rv = interrupt(_interrupt_card(ctx, "todo-end-card", {
            "task_id": board["task_id"], "task_title": board["title"],
            "stats": stats, "unfinished": unfinished,
        }))
        if rv.get("action") == "cancelled":
            return "已取消结束任务"
        return rv.get("summary", "任务已结束")

    @tool(
        "task_overview",
        description="查看派发任务概览（纯文本：各任务状态统计，不弹卡）。task 可选，缺省给出全部派发中任务。",
    )
    def task_overview(
        task: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "task_overview", tool_call_id, {"task": task or ""}) as db:
            if (task or "").strip():
                found, hint = _resolve_dispatcher_task(db, ctx.user_id, task)
                if found is None:
                    return hint
                boards = [todo_svc.board(db, found.task_id, ctx.user_id)]
            else:
                from sqlalchemy import select

                from ...persistence.models import BizTask

                tasks = db.scalars(
                    select(BizTask)
                    .where(BizTask.dispatcher_id == ctx.user_id, BizTask.status == "open")
                    .order_by(BizTask.created_at.desc())
                    .limit(10)
                ).all()
                boards = [todo_svc.board(db, t.task_id, ctx.user_id) for t in tasks]
        if not boards:
            return "你当前没有进行中的派发任务。"
        zh = {"pending": "待反馈", "returned": "已退回", "submitted": "待审批",
              "na_pending": "不涉及待确认", "completed": "已完成", "na_closed": "已关闭"}
        lines = []
        for b in boards:
            stat = "、".join(f"{zh.get(s, s)} {n}" for s, n in sorted(b["stats"].items())) or "无待办"
            s = b["stats"]
            if s and sum(s.values()) == s.get("completed", 0) + s.get("na_closed", 0):
                stat += "（已全部完成，可结束闭环，end_task_flow 一键结束）"
            lines.append(f"「{b['title']}」：{stat}")
        return "派发任务概览（进行中）：" + "；".join(lines)

    @tool(
        "dispatch_task",
        description=(
            "AI 派发任务（通用待办或模板小流程，支持批量）。description=任务标题/说明；"
            "assignees=处理人名称列表（用户昵称/角色/部门/公司/用户组，须来自用户消息，不得编造）；"
            "template_key=模板键或模板名称（risk_plan=风险应对方案 / risk_item=风险应对项；"
            "用户点名某个模板/填报模板时，用户说的名称可直接传入，后端自动按名称匹配出 key；"
            "不确定用哪个模板时不传，确认卡上可选任务类型让用户自己挑）；"
            "anchor=模板锚点对象，风险流程必须传 {\"company\": 企业名}（标题按它渲染），"
            "用户话语中提到的企业名放这里；due=截止日期 YYYY-MM-DD（必填）；"
            "review_required=是否需审核（模板任务不传则按模板默认）；"
            "parent_task_id=父任务 ID（递归流程用，可选）；period=归属期 YYYY-MM（可选）。"
            "描述、处理人、截止时间三项必须齐备才调用本工具，任一缺失先向用户追问；"
            "调用后弹确认卡由用户确认（批量>10 人卡上有警示）。"
        ),
    )
    def dispatch_task(
        description: str,
        assignees: list[str],
        template_key: str | None = None,
        due: str | None = None,
        review_required: bool | None = None,
        anchor: dict | None = None,
        parent_task_id: str | None = None,
        period: str | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        from ...core import errors as _errors
        from ...services import task_templates

        args = {"description": description, "assignees": assignees, "template_key": template_key,
                "due": due, "review_required": review_required, "anchor": anchor,
                "parent_task_id": parent_task_id, "period": period}
        # 派发必要参数硬校验（三要素缺一不弹卡，返回追问话术由用户补齐）
        missing = [lbl for lbl, val in (
            ("待办描述", (description or "").strip()),
            ("截止时间", (due or "").strip()),
            ("处理人", assignees or None),
        ) if not val]
        if missing:
            return ("缺少必要信息：" + "、".join(missing)
                    + "。请先向用户追问补齐（截止时间格式 YYYY-MM-DD，处理人用 @ 指定），不要自行编造。")
        with tool_scope(ctx, "dispatch_task", tool_call_id, args) as db:
            resolved = identity_svc.resolve_labels_to_users(assignees)
            if not resolved["assignees"]:
                unk = "、".join(resolved["unknown"]) or "（空）"
                return f"未能在组织目录中解析到处理人：{unk}。请让用户用 @ 明确处理人后重试。"
            tpl_name, scene = "", "generic"
            if template_key:
                try:
                    template_key, tpl_name = _resolve_template_key(db, template_key)
                except _errors.AppError as exc:
                    return f"{exc.message}。请让用户确认模板名，或直接出卡让用户在卡上选择任务类型。"
                if template_key.startswith("dyn_"):
                    scene = "dynamic_fill"
                    if review_required is None:
                        review_required = True  # 动态填报默认两段式审核
                else:
                    tpl = task_templates.get_template(template_key)
                    scene = tpl.get("scene", "generic")
                    if review_required is None:
                        review_required = bool(tpl.get("review_required"))
            if review_required is None:
                review_required = False
            # L0 免卡白名单（仅 dev 配置生效）：单人 + scene 命中直接落库
            if (scene in settings.auto_dispatch_scene_list
                    and len(resolved["assignees"]) == 1 and not template_key):
                summary = do_dispatch(
                    db, user_id=ctx.user_id, session_id=ctx.session_id, title=description,
                    assignees=resolved["assignees"], due=due,
                    review_required=bool(review_required), note="",
                )
                return summary
            note_parts = []
            if tpl_name:
                note_parts.append(f"模板流程：{tpl_name}")
            if anchor:
                note_parts.append(f"锚点：{anchor}")
            if len(resolved["assignees"]) > 10:
                note_parts.append(f"⚠️ 本次将派发给 {len(resolved['assignees'])} 人，请确认影响面")
            payload = _interrupt_card(ctx, "arrange-confirm", {
                "title": description,
                "note": "；".join(note_parts),
                "due": due,
                "review_required": bool(review_required),
                "assignees": resolved["assignees"],
                "unknown": resolved["unknown"],
                "template_key": template_key,
                "anchor": anchor,
                "parent_task_id": parent_task_id,
                "period": period,
                # 两步卡（与 arrange_task 同模式）：模板未定时用户可在卡上选任务类型
                "editable": True,
                "templates": _template_options(db),
            })
            rv = interrupt(payload)  # resume 后从此返回；解析为只读，重放安全
        if rv.get("action") == "cancelled":
            return "用户取消了本次派发。如需调整（如更换处理人、换模板），请在用户补充后重新调用。"
        return rv.get("summary", f"已派发「{description}」")

    @tool(
        "fill_todo_payload",
        description=(
            "处理人用 AI 代填待办草稿（模板任务的表单内容）。todo_id 用 list_my_todos 查询；"
            "field_values=字段键值对（键名来自任务模板，如 risk_desc/measures/owner/deadline），"
            "值从对话上下文（问数结果/报告/用户说明）提取，不得编造；submit=是否同时发起提交"
            "（默认 false 只存草稿并报告必填缺失项，true 时弹完成确认卡由用户确认提交）。"
        ),
    )
    def fill_todo_payload(
        todo_id: str,
        field_values: dict,
        submit: bool = False,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        from ...core.errors import AppError
        from ...persistence.models import BizTask, BizTodo
        from ...services import task_templates

        args = {"todo_id": todo_id, "field_values": field_values, "submit": submit}
        with tool_scope(ctx, "fill_todo_payload", tool_call_id, args) as db:
            t = db.get(BizTodo, todo_id)
            if t is None:
                from types import SimpleNamespace

                live = [x for x in todo_svc.list_todos(
                    db, SimpleNamespace(user_id=ctx.user_id), "assignee")
                    if x["status"] in ("pending", "returned")]
                cand = "；".join(f"{x['title']}（todo_id={x['todo_id']}）" for x in live[:10])
                return (f"待办不存在。请先用上面的候选重新调用（当前进行中的待办："
                        f"{cand or '无'}）")
            if t.assignee_id != ctx.user_id:
                return "仅待办处理人本人可代填"
            if t.status not in ("pending", "returned"):
                return f"待办当前状态（{t.status}）不允许修改填报内容"
            task = db.get(BizTask, t.task_id)
            merged = {**(t.payload or {}), **field_values}
            todo_svc.save_payload(db, todo_id, ctx.user_id, merged)
            problems: list[str] = []
            if task is not None and task.template_key:
                try:
                    task_templates.validate_payload(
                        task_templates.get_template(task.template_key), merged)
                except AppError as e:
                    problems.append(e.message)
            filled = sum(1 for v in merged.values()
                         if v not in (None, "", [], {}))
            title, review_required = t.title, bool(task.review_required) if task else False
        report = f"已保存草稿「{title}」（已填 {filled} 项）。"
        if problems:
            report += "尚缺：" + "；".join(problems) + "。请补齐后再提交。"
        else:
            report += "必填项已齐全，可提交。"
        if submit:
            if problems:
                return report + "（本次未发起提交）"
            rv = interrupt(_interrupt_card(ctx, "todo-done-card", {
                "todo_id": todo_id, "title": title, "review_required": review_required,
            }))
            if rv.get("action") == "cancelled":
                return report + "（用户取消了提交）"
            return rv.get("summary", report)
        return report

    @tool(
        "pre_review_todos",
        description=(
            "派发人 AI 预审：对「待审批」提交逐条生成审核建议（必填齐全性/填报内容完整性检查），"
            "纯文本返回不弹卡、不自动审核。task 可选（任务标题或 task_id，缺省审全部）。"
            "用户确认后用 review_submissions 出卡执行审核。"
        ),
    )
    def pre_review_todos(
        task: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        from ...core.errors import AppError
        from ...persistence.models import BizTask, BizTodo
        from ...services import task_templates

        with tool_scope(ctx, "pre_review_todos", tool_call_id, {"task": task or ""}) as db:
            items = todo_svc.dispatch_queue(db, ctx.user_id, ["submitted"])
            if (task or "").strip():
                found, hint = _resolve_dispatcher_task(db, ctx.user_id, task)
                if found is None:
                    return hint
                items = [it for it in items if it["task_id"] == found.task_id]
            if not items:
                return "当前没有待审核的提交。"
            lines, pass_cnt, warn_cnt = [], 0, 0
            for it in items:
                problems: list[str] = []
                t = db.get(BizTodo, it["todo_id"])
                tk = db.get(BizTask, t.task_id) if t else None
                if tk is not None and tk.template_key:
                    try:
                        task_templates.validate_payload(
                            task_templates.get_template(tk.template_key), t.payload or {})
                    except AppError as e:
                        problems.append(e.message)
                if not (it.get("completion_text") or (t and t.payload)):
                    problems.append("无完成说明且无填报内容")
                lamp = (t.lamp_agg or {}) if t else {}
                if lamp.get("r"):
                    problems.append(f"含红灯 {lamp['r']} 项")
                who = it.get("assignee_name") or it.get("todo_id")
                if problems:
                    warn_cnt += 1
                    lines.append(f"「{it['title']}」（{who}）：建议关注——{'；'.join(problems)}")
                else:
                    pass_cnt += 1
                    lines.append(f"「{it['title']}」（{who}）：建议通过")
        head = f"AI 预审 {len(items)} 条提交：建议通过 {pass_cnt}、建议关注 {warn_cnt}。"
        tail = "确认后可让我出审核卡逐条处理。"
        return head + "\n" + "\n".join(lines[:20]) + ("\n…" if len(lines) > 20 else "") + tail

    # 工具收敛（业务要求）：list_companies/dispatch_generic_task/query_task_stats 暂不注册；
    # 需求B（通用 AI 待办）注册：安排/处理四件套 + 派发人五件套（审核/确认/催办/追加/结束）+ 概览；
    # 08 设计（AI 派发/处理闭环）：dispatch_task / fill_todo_payload / pre_review_todos
    return [
        arrange_task, dispatch_task, fill_todo_payload, pre_review_todos,
        list_my_todos, request_todo_done, request_todo_na,
        review_submissions, confirm_na_requests, remind_pending,
        append_task_assignees, end_task_flow, task_overview,
    ]


_token_cache: dict = {"token": "", "expires_at": 0.0}  # 服务账号 accessToken 缓存
_token_lock = threading.Lock()


def _scms_login() -> str:
    """服务账号登录换 accessToken（yudao 协议：POST /admin-api/system/auth/login）。"""
    req = urllib.request.Request(
        f"{settings.scms_auth_base}/admin-api/system/auth/login",
        data=json.dumps({
            "username": settings.scms_login_username,
            "password": settings.scms_login_password,
            "captchaVerification": "",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8")).get("data") or {}
    token = data.get("accessToken") or ""
    expires = float(data.get("expiresTime") or 0) / 1000.0  # ms → s
    if not token:
        raise RuntimeError("SCMS 登录响应缺少 accessToken")
    _token_cache.update(token=token, expires_at=expires)
    logger.info("SCMS 服务账号登录成功，token 过期时间戳=%s", expires)
    return token


def _service_token(force_refresh: bool = False) -> str:
    """服务账号凭证：配置了自动登录+缓存续期；否则回落静态 token。仅无用户上下文时使用。"""
    if not settings.scms_login_username:
        return settings.scms_nl2sql_token
    with _token_lock:
        if not force_refresh and _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
            return _token_cache["token"]
        return _scms_login()


def _nl2sql_token(force_refresh: bool = False) -> str:
    """SCMS 凭证单点（透传模式）：优先当前用户 token，无用户上下文回落服务账号。"""
    return token_auth.current_user_token() or _service_token(force_refresh)


def _scms_call_with_fallback(tag: str, do_call):
    """SCMS 出站统一凭证回落：优先当前用户 token，401（token 失效/越权）回落服务账号重试一次。

    do_call(token) -> 结果；仅首轮收到 401 时回落重试，非 401 HTTPError 与其他异常直接抛出。
    统一封装问数/问数流/KB 文件代理等链路的重复回落循环。
    """
    user_token = token_auth.current_user_token()
    for attempt in (0, 1):
        token = user_token if attempt == 0 and user_token else _service_token(force_refresh=attempt == 1)
        try:
            return do_call(token)
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                logger.info("%s 401（用户 token 失效或越权），回落服务账号重试", tag)
                continue
            raise
    raise RuntimeError("unreachable")


def _norm_year(v) -> int | None:
    """年份宽松归一：模型缺参时常传空串。'' / None → None；int 直取；
    字符串取首个 4 位数字（'2025年' → 2025），取不到 → None（走表单卡）。"""
    if v is None or v == "":
        return None
    if isinstance(v, int):
        return v
    m = re.search(r"\d{4}", str(v))
    return int(m.group()) if m else None


def _embed_tenant() -> int | None:
    """报告嵌入卡租户号：从当前用户 token 的缓存身份取（未知给 null，前端跳过该参数）。"""
    ident = token_auth.identity_of(token_auth.current_user_token())
    try:
        return int(ident.tenant_id) if ident and ident.tenant_id else None
    except (TypeError, ValueError):
        return None


def _token_source() -> str:
    """当前生效凭证来源（诊断用）：用户token / 服务账号 / 静态token。"""
    if token_auth.current_user_token():
        return "用户token"
    return "服务账号" if settings.scms_login_username else "静态token"


def _nl2sql_call(query: str) -> dict:
    """调用 SCMS 问数服务（REST）。凭证策略走 _scms_call_with_fallback 统一回落
    （只读查询不中断会话；将来提供方上 MCP 后仅替换此函数，工具契约不变）。"""

    def _call(token: str) -> dict:
        req = urllib.request.Request(
            settings.scms_nl2sql_url,
            data=json.dumps({"query": query}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-SCMS-Access-Token": token,
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body.get("result") or {}

    return _scms_call_with_fallback("问数", _call)


def _query_data_call(question: str, timeout: int = 120, session_id: str | None = None,
                     on_content=None, enable_web_search: bool = False,
                     space_ids: list[int] | None = None,
                     file_ids: list[int] | None = None,
                     folder_ids: list[int] | None = None,
                     on_sources=None) -> tuple[str, list[dict], dict, str | None]:
    """调用智能问数评估流（SSE）：POST query 原问，逐事件解析。

    事件契约（与 workbench aiChat 同源）：streaming_content.data.content 追加正文；
    chart.data 为结构化图表（labels/series/xAxisName/yAxisName）；error 记录
    （非致命——该服务 error 后仍会发 done，有正文时正文优先）；
    tool_end.output 携带溯源元数据（citations 知识库文件引用 / citation_chunks 命中片段 /
    web_results 联网结果 / route 路由 / web_ok 联网链路状态），收集进 sources 供前端结构化溯源渲染。
    on_content：正文片段实时回调（query_data 用来边收边直通流式输出）。
    on_sources：溯源增量实时回调（tool_end 吸收到新 citations/chunks/webs 时触发，
    传当前 sources 快照的深拷贝——query_data 用来 tool/progress(sources_ready) 即时下发前端）。
    enable_web_search：联网搜索开关（True 时随请求体透传上游；False 不带键，兼容旧上游）。
    space_ids/file_ids/folder_ids：本轮知识库范围（库/文件/文件夹 id 数组），非空时按
    spaceIds/fileIds/folderIds 键原封不动随请求体透传上游限定检索范围；空=不限定。
    token 策略同 nl2sql：用户 token 401 回落服务账号重试一次。
    返回 (正文全文, 图表列表, 溯源sources, 错误信息)。
    """
    url = settings.scms_query_data_url
    body: dict = {"query": question, "session_id": session_id}
    if enable_web_search:
        body["enable_web_search"] = True
    if space_ids:
        body["spaceIds"] = space_ids
    if file_ids:
        body["fileIds"] = file_ids
    if folder_ids:
        body["folderIds"] = folder_ids

    def _call(token: str) -> tuple[str, list[dict], dict, str | None]:
        # 外部接口审计日志（重要链路，对齐 uvicorn access log 风格）：URL/入参/凭证一目了然
        logger.info(
            "外部接口 → \"POST %s HTTP/1.1\" 发起 token=%s\n  入参: %s",
            url, _token_source(), json.dumps(body, ensure_ascii=False),
        )
        t0 = time.time()
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-SCMS-Access-Token": token,
                "Accept": "text/event-stream",
            },
        )
        try:
            text_parts: list[str] = []
            charts: list[dict] = []
            error_msg: list[str] = []
            sources: dict = {}  # tool_end 溯源元数据（citations/citation_chunks/web_results/route/web_ok）
            prev_snap = [-1, -1, -1]  # 上次 on_sources 回调时的 (citations/chunks/webs) 数量快照

            def _absorb(lines: list[str]) -> None:
                piece = _absorb_sse_event_collect(lines, text_parts, charts, error_msg, sources)
                if piece and on_content is not None:
                    on_content(piece)
                # 溯源增量即时下发：tool_end 吸收到新 citations/citation_chunks/web_results 就回调
                if on_sources is not None:
                    snap = [len(sources.get("citations") or []),
                            len(sources.get("citation_chunks") or []),
                            len(sources.get("web_results") or [])]
                    if snap != prev_snap:
                        prev_snap[:] = snap
                        # json round-trip：深拷贝 + 提前验证可序列化（store 落库为 json）
                        on_sources(json.loads(json.dumps(sources, ensure_ascii=False)))

            with urllib.request.urlopen(req, timeout=timeout) as resp:
                buf: list[str] = []
                for raw in resp:
                    line = raw.decode("utf-8").rstrip("\n")
                    if line.strip() == "":
                        # 空行 = SSE 事件块结束
                        if buf:
                            _absorb(buf)
                            buf = []
                        continue
                    buf.append(line)
                if buf:
                    _absorb(buf)
            answer = "".join(text_parts)
            # 完成日志：状态码/耗时/产量 + 回答全文（外部接口全量留痕，便于追溯）
            logger.info(
                "外部接口 ← \"POST %s HTTP/1.1\" %d OK 耗时=%.1fs 正文=%d字 图表=%d个 来源=%d/%d/%d 错误=%s\n  回答: %s",
                url, resp.status, time.time() - t0, len(answer), len(charts),
                len(sources.get("citations") or []), len(sources.get("web_results") or []),
                len(sources.get("citation_chunks") or []),
                error_msg[0] if error_msg else None, answer or "（空）",
            )
            return answer, charts, sources, (error_msg[0] if error_msg else None)
        except urllib.error.HTTPError as exc:
            logger.warning(
                "外部接口 ← \"POST %s HTTP/1.1\" %d 失败 耗时=%.1fs token=%s",
                url, exc.code, time.time() - t0, _token_source(),
            )
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning(
                "外部接口 ← \"POST %s HTTP/1.1\" 连接失败 耗时=%.1fs err=%r",
                url, time.time() - t0, exc,
            )
            raise

    return _scms_call_with_fallback("问数流", _call)


def _kb_proxy_path(file_id, attachment: bool = False) -> str:
    """KB 文件我方代理端点相对路径（前端拼 API_BASE 调用，经我们后端鉴权转发网关）。"""
    return f"/sessions/kb-files/{file_id}/{'download' if attachment else 'preview'}"


def _absorb_sse_event_collect(lines: list[str], text_parts: list[str], charts: list[dict],
                              error_msg: list[str], sources: dict) -> str:
    """单个 SSE 事件块 → 正文片段 / 图表 / 错误 / 溯源元数据；返回本次新增的正文片段。解析失败静默。"""
    sse_event = ""
    data_lines: list[str] = []
    for ln in lines:
        if ln.startswith("event:"):
            sse_event = ln[6:].strip()
        elif ln.startswith("data:"):
            data_lines.append(ln[5:].strip())
    if not data_lines:
        return ""
    try:
        ev = json.loads("\n".join(data_lines))
    except ValueError:
        return ""
    event_type = ev.get("event_type") or sse_event
    data = ev.get("data") or {}
    if event_type == "streaming_content":
        piece = str(data.get("content") or "")
        if piece:
            text_parts.append(piece)
        return piece
    if event_type == "chart":
        if isinstance(data, dict) and data.get("labels") is not None:
            charts.append(data)
    elif event_type == "tool_end":
        # 溯源元数据平铺在 output（防御性兼容嵌套 data.output）：
        # citations=知识库文件级引用（历史形态放 output.data，按 file_id/file_name 形状过滤防误吸 SQL 行），
        # citation_chunks=片段级命中（含 cite_index 供正文 [n] 角标定位），web_results=联网结果，
        # route/web_ok=链路状态。多次 tool_end 按 file_id / (file_id,chunk_index) / url 去重合并。
        output = data.get("output") if isinstance(data.get("output"), dict) else data
        cits = output.get("citations")
        if not cits and isinstance(output.get("data"), list):
            cits = [x for x in output["data"]
                    if isinstance(x, dict) and ("file_id" in x or "file_name" in x)]
        cits = [c for c in (cits or []) if isinstance(c, dict)]
        chunks = [c for c in (output.get("citation_chunks") or []) if isinstance(c, dict)]
        webs = [w for w in (output.get("web_results") or []) if isinstance(w, dict)]
        if cits:
            merged = sources.setdefault("citations", [])
            seen = {str(c.get("file_id")) for c in merged}
            for c in cits:
                fid = c.get("file_id")
                if fid is None or str(fid) in seen:
                    continue
                seen.add(str(fid))
                merged.append({
                    "file_id": fid,
                    "file_name": str(c.get("file_name") or c.get("filename") or f"文档{fid}"),
                    # 预览/下载走我方代理端点（前端带平台 token 调我们，后端转发网关），不用网关相对路径
                    "preview_url": _kb_proxy_path(fid),
                    "download_url": _kb_proxy_path(fid, attachment=True),
                })
        if chunks:
            merged = sources.setdefault("citation_chunks", [])
            seen = {(str(c.get("file_id")), c.get("chunk_index")) for c in merged}
            for c in chunks:
                fid = c.get("file_id")
                cidx = c.get("chunk_index")
                if fid is None or (str(fid), cidx) in seen:
                    continue
                seen.add((str(fid), cidx))
                try:
                    score = float(c.get("score") or 0)
                except (TypeError, ValueError):
                    score = 0.0
                merged.append({
                    "file_id": fid,
                    "file_name": str(c.get("file_name") or c.get("filename") or f"文档{fid}"),
                    "chunk_index": int(cidx or 0),
                    "cite_index": int(c.get("cite_index") or 0),
                    "chunk_text": str(c.get("chunk_text") or ""),
                    # 非有限值（NaN/Inf）按缺失处理：json.dumps 会产出非法 JSON 字面量，落库即 500
                    "score": score if score == score else 0.0,
                })
        if webs:
            merged = sources.setdefault("web_results", [])
            seen = {str(w.get("url")) for w in merged}
            for w in webs:
                wurl = str(w.get("url") or "")
                if not wurl or wurl in seen:
                    continue
                seen.add(wurl)
                merged.append({"title": str(w.get("title") or wurl), "url": wurl,
                               "snippet": str(w.get("snippet") or "")[:200]})
        if output.get("route"):
            sources["route"] = str(output["route"])
        if "web_ok" in output:
            sources["web_ok"] = bool(output["web_ok"])
    elif event_type == "error" and not error_msg:
        msg = str(data.get("user_message") or data.get("message")
                  or ev.get("message") or "")
        if msg:
            error_msg.append(msg)
    return ""


def _build_chart_option(chart_type: str, title: str, x_field: str, y_fields: list[str],
                        group_field: str | None, columns: list, rows: list):
    """把问数结果确定性组装为 ECharts option（服务端取数，模型只选类型与列）。
    返回 (option, err)；同类目多行按 SUM 聚合，类目超 20 截断并在标题注明。"""
    if chart_type not in ("bar", "line", "pie"):
        return None, f"不支持的图表类型 {chart_type}（仅 bar/line/pie）"
    if x_field not in columns:
        return None, f"x_field「{x_field}」不在结果列中"
    ys = [y for y in (y_fields or []) if y]
    bad = [y for y in ys if y not in columns]
    if bad:
        return None, f"y_fields {bad} 不在结果列中"
    if not ys:
        return None, "缺少 y_fields 数值列"
    if group_field is not None and group_field not in columns:
        return None, f"group_field「{group_field}」不在结果列中"

    def num(v):
        # 非有限值（NaN/Inf）按缺失处理：json.dumps 会产出非法 JSON 字面量，落库即 500
        try:
            import math

            f = float(str(v).replace(",", ""))
            return f if math.isfinite(f) else None
        except (TypeError, ValueError):
            return None

    dicts = [r if isinstance(r, dict) else dict(zip(columns, r)) for r in rows]
    cats: list[str] = []
    groups: list[str] = []
    acc: dict = {}  # (y, group, cat) -> sum
    for r in dicts:
        xv = r.get(x_field)
        cat = "" if xv is None else str(xv)
        if cat not in cats:
            cats.append(cat)
        g = ""
        if group_field is not None:
            gv = r.get(group_field)
            g = "" if gv is None else str(gv)
            if g not in groups:
                groups.append(g)
        for y in ys:
            v = num(r.get(y))
            if v is None:
                continue
            acc[(y, g, cat)] = acc.get((y, g, cat), 0.0) + v

    note = ""
    if len(cats) > 20:
        cats = cats[:20]
        note = "（仅展示前 20 项）"
    full_title = f"{title}{note}" if note else title

    if chart_type == "pie":
        y = ys[0]
        data = [{"name": c, "value": acc.get((y, "", c))} for c in cats
                if acc.get((y, "", c)) is not None]
        if not data:
            return None, "没有可画的数值数据"
        return {
            "title": {"text": full_title, "left": "center"},
            "tooltip": {"trigger": "item"},
            "series": [{"type": "pie", "radius": "62%", "data": data}],
        }, None

    series = []
    if group_field is not None:
        for y in ys:
            for g in groups:
                name = f"{g}·{y}" if len(ys) > 1 else g
                series.append({"name": name, "type": chart_type,
                               "data": [acc.get((y, g, c)) for c in cats]})
    else:
        for y in ys:
            series.append({"name": y, "type": chart_type,
                           "data": [acc.get((y, "", c)) for c in cats]})
    option = {
        "title": {"text": full_title, "left": "center"},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 48, "right": 16, "top": 44,
                 "bottom": 36 if len(series) > 1 else 24, "containLabel": True},
        "xAxis": {"type": "category", "data": cats},
        "yAxis": {"type": "value"},
        "series": series,
    }
    if len(series) > 1:
        option["legend"] = {"bottom": 0}
    return option, None


def build_data_tools(ctx: ToolCtx) -> list:
    """问数与报告跳转工具：query_data 直接把用户原话交给智能问数流并原样返回答案；
    报告卡片只出选项并跳转目标系统。"""

    @tool(
        "query_data",
        description="智能问答：用户想查询数据、统计指标、看数据对比或趋势时调用，问数和知识问答都可以通过这个解决"
        "（如'信投智科2025年净利润是多少'）。question 传用户的原始问题即可"
        "（后端会强制改用用户本轮的原话发起查询，不依赖转述）。"
        "用户明确要求联网搜索/上网查资料时传 enable_web_search=true（输入框开关打开时系统自动启用，无需传参）。"
        "答案会由系统直接实时流式呈现给用户——工具返回后你不要再复述答案内容，"
        "按工具返回的指示收尾即可。",
    )
    def query_data(
        question: str = "",
        enable_web_search: bool = False,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        # 强制使用本轮用户原话：模型转述/裁剪过的参数不可信（严格契约）
        q = ctx.raw_input.strip() or question.strip()
        # 联网开关：本轮用户开关（ctx，来自输入框）优先；模型仅在用户明确要求联网时显式传 true
        web = bool(ctx.enable_web_search) or bool(enable_web_search)
        # 直通通道：正文边收边直通流式输出（统一生命周期封装）
        sink = ctx.answer_stream
        stream = ToolAnswerStream(sink, "query_data")

        with tool_scope(ctx, "query_data", tool_call_id, {"question": q, "enable_web_search": web}):
            # 溯源即时下发：tool_end 一到（不等流结束）就经 tool/progress(sources_ready) 推给前端，
            # 流式期间即可渲染来源卡/命中片段面板（事件 ignorable，旧端无感）
            def _on_sources(snap: dict) -> None:
                if hasattr(sink, "emit_tool_progress"):
                    sink.emit_tool_progress(None, "query_data", "sources_ready",
                                            "已获取溯源来源", sources=snap)

            try:
                if hasattr(sink, "emit_tool_progress"):
                    sink.emit_tool_progress(None, "query_data", "waiting_upstream", "正在连接智能问数服务…")
                answer, charts, sources, error_msg = _query_data_call(
                    q, session_id=ctx.session_id, on_content=stream.append, enable_web_search=web,
                    space_ids=ctx.space_ids, file_ids=ctx.file_ids, folder_ids=ctx.folder_ids,
                    on_sources=_on_sources)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                logger.warning("问数服务不可达 sid=%s err=%r", ctx.session_id, exc)
                return f"数据查询服务暂不可用，请稍后再试（{exc}）"
            except Exception as exc:
                logger.warning("问数服务异常 sid=%s err=%r", ctx.session_id, exc)
                return f"数据查询失败：{exc}"
            # 图表围栏与结构化溯源围栏作为正文尾部补发（前端 md 围栏分别渲染图表卡/溯源卡）；
            # 随后固化本段直通正文
            if charts and stream.streamed:
                for c in charts:
                    stream.append("\n\n```chart\n" + json.dumps(c, ensure_ascii=False) + "\n```")
            if sources and stream.streamed:
                stream.append("\n\n```sources\n" + json.dumps(sources, ensure_ascii=False) + "\n```")
            stream.end()
        if stream.streamed:
            # 直通场景：答案已流式直出，交付物由 tool_scope 收尾时统一补录 Langfuse，此处无需处理
            return ("答案已直接流式呈现给用户（含图表），无需复述。"
                    "不要再输出任何正文内容，直接结束本轮即可。")
        if not answer and not charts and not sources:
            return f"未查询到相关数据（{error_msg or '请换一种问法重试'}）。"
        # 无直通通道（异常场景兜底）：图表/溯源围栏随文本一并返回
        parts = [answer.rstrip()] if answer else []
        for c in charts:
            parts.append("```chart\n" + json.dumps(c, ensure_ascii=False) + "\n```")
        if sources:
            parts.append("```sources\n" + json.dumps(sources, ensure_ascii=False) + "\n```")
        return "\n\n".join(parts)

    # ── 旧问数链路（nl2sql REST + 确定性出图）：按业务要求暂注释，恢复时取消注释
    #    并把下方工具清单加回 nl2sql_query / draw_chart。契约（事件、前端卡）保留未删。
    @tool(
        "nl2sql_query",
        description="用自然语言查询被投企业财务数据（净利润、营收等财务指标，支持按公司/年度/期间）。"
        "query 为自然语言问题，如：信投智科2025年归母净利润是多少。",
    )
    def nl2sql_query(
        query: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "nl2sql_query", tool_call_id, {"query": query}):
            try:
                result = _nl2sql_call(query)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                logger.warning("问数服务不可达 sid=%s err=%r", ctx.session_id, exc)
                return f"数据查询服务暂不可用，请稍后再试（{exc}）"
            except Exception as exc:
                logger.warning("问数服务异常 sid=%s err=%r", ctx.session_id, exc)
                return f"数据查询失败：{exc}"
        if not result.get("success"):
            return f"查询失败：{result.get('error') or '服务端未返回原因'}"
        rows = result.get("rows") or []
        columns = result.get("columns") or []
        # 供 draw_chart 确定性组装：数字不经过模型第二道手
        ctx.extra["last_nl2sql"] = {
            "columns": [str(c) for c in columns],
            "rows": [dict(r) if isinstance(r, dict) else r for r in rows],
        }
        if not rows:
            return (f"未查询到相关数据。请确认公司名称、年度或指标是否正确，可换一种问法重试。\n"
                    f"生成的 SQL：\n```\n{result.get('sql', '')}\n```")
        # 结果行硬顶，防撑爆模型上下文；输出为 GFM 表格（前后端 markdown 渲染直出）
        shown = rows[:50]
        col_names = [str(c) for c in columns] if columns else [f"col{i + 1}" for i in range(len(shown[0]) if isinstance(shown[0], list) else len(shown[0]))]
    
        def cells(r) -> list[str]:
            return [str(v) for v in (r if isinstance(r, list) else list(r.values()))]
    
        md = ["| " + " | ".join(col_names) + " |",
              "| " + " | ".join("---" for _ in col_names) + " |"]
        md += ["| " + " | ".join(cells(r)) + " |" for r in shown]
        more = f"（仅展示前 {len(shown)}/{len(rows)} 行）" if len(rows) > len(shown) else ""
        return (f"命中 {len(rows)} 行{more}。生成的 SQL：\n```\n{result.get('sql', '')}\n```\n"
                + "\n".join(md))
    
    @tool(
        "draw_chart",
        description="把最近一次 nl2sql_query 的结果画成图表卡（bar/line/pie）。"
        "x_field/y_fields/group_field 必须是那次查询结果表的真实列名：趋势用 line、类目对比用 bar、"
        "占比用 pie；y_fields 选数值列；group_field 可选（按其列值拆多系列，如按指标名分线）。"
        "出图后正文只写简短结论，不必再贴大表。",
    )
    def draw_chart(
        chart_type: str,
        title: str,
        x_field: str,
        y_fields: list[str],
        group_field: str | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"chart_type": chart_type, "title": title, "x_field": x_field,
                "y_fields": y_fields, "group_field": group_field}
        with tool_scope(ctx, "draw_chart", tool_call_id, args):
            last = ctx.extra.get("last_nl2sql")
            if not last:
                return "本回合还没有问数结果，请先调用 nl2sql_query 再画图"
            option, err = _build_chart_option(chart_type, title, x_field, y_fields,
                                              group_field, last["columns"], last["rows"])
            if err:
                cols = "、".join(str(c) for c in last["columns"])
                return f"{err}。结果表的列：{cols}。请用真实列名重试"
            ctx.emit_event("chart/echarts-card", {
                "card_id": uuid.uuid4().hex,
                "chart_type": chart_type if chart_type in ("bar", "line", "pie") else "bar",
                "title": title,
                "option": option,
                "version": 1,
            })
        return f"已生成图表卡「{title}」（{chart_type}），正文给简短结论即可，不必重复表格数据"

    @tool(
        "open_post_report",
        description="生成企业年度项目管理报告（Word 版）：出一张两步卡片——参数齐全（company+year）时"
        "卡片直接进入生成；不全时第一步出参数表单（公司下拉+年份），用户提交后卡片自动开始生成。"
        "用户要报告时立即调用，已知 company/year 作预填，不要纯文本反问、不要臆造参数。"
        "每次用户提出生成报告都必须重新调用本工具（即使历史里已生成过或正在生成中——"
        "对话里的旧卡片用户可能已看不到），绝不要用纯文本回复'正在生成中'来代替出卡。",
    )
    def open_post_report(
        company: str = "",
        year: int | str | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        from ..scms import companies as company_book

        yr = _norm_year(year)
        hit = company_book.resolve(company or "")
        args = {"company": company, "year": yr}
        with tool_scope(ctx, "open_post_report", tool_call_id, args):
            if hit is not None and yr is not None:
                ctx.emit_event("report/embed-card", {
                    "card_id": uuid.uuid4().hex,
                    "report_type": "annual",
                    "title": f"{hit['shortName']} {yr} 年度项目管理报告",
                    "company_id": hit["companyId"],
                    "company_name": hit["shortName"],
                    "period": str(yr),
                    "url": (f"{settings.report_embed_base}/embed/word-generator"
                            f"?templateId={settings.report_tpl_annual_id}"
                            f"&enterpriseId={hit['companyId']}&year={yr}"
                            f"&company={urllib.parse.quote(hit['orgName'])}"),
                    "tenant_id": _embed_tenant(),
                    "user_id": ctx.user_id,
                    "mode": "embed",
                    "version": 1,
                })
                return (f"已创建 {hit['shortName']} {yr} 年度项目管理报告的生成任务"
                        f"（企业ID={hit['companyId']}），报告正在卡片内异步生成中，"
                        "请告知用户稍候，完成后可直接在卡片查看，无需等待")
            ctx.emit_event("report/embed-card", {
                "card_id": uuid.uuid4().hex,
                "report_type": "annual",
                "title": "年度项目管理报告 · 选择参数",
                "company_id": 0,
                "company_name": (company or "").strip(),
                "period": str(yr) if yr is not None else "",
                "url": "",
                "tenant_id": _embed_tenant(),
                "user_id": ctx.user_id,
                "mode": "form",
                "companies": [{"company_id": c["companyId"], "short_name": c["shortName"],
                               "org_name": c["orgName"]} for c in company_book._COMPANY_TREE],
                "embed_base": settings.report_embed_base,
                "template_id": settings.report_tpl_annual_id,
                "version": 1,
            })
        return ("表单卡已出。给用户的回复保持一句话以内（例如：请在上方卡片选择企业与年份，"
                "选完点击开始生成），不要罗列表单字段、不要复述卡片内已有的说明、不要再补充替代操作方式")

    @tool(
        "open_fin_risk_report",
        description="生成财务风险报告（PPT 版）：参数只有报告期间（month，格式 YYYY-MM）。"
        "出一张两步卡片——期间明确时卡片直接进入生成；不明确时第一步出参数表单（年月），"
        "用户提交后卡片自动开始生成。用户要报告时立即调用，已知年月作预填，"
        "不要纯文本反问、不要臆造参数。"
        "每次用户提出生成报告都必须重新调用本工具（即使历史里已生成过或正在生成中），"
        "绝不要用纯文本回复'正在生成中'来代替出卡。",
    )
    def open_fin_risk_report(
        month: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        period = (month or "").strip()
        valid_period = bool(re.fullmatch(r"\d{4}-\d{2}", period)) and 1 <= int(period[5:7]) <= 12
        args = {"month": period}
        ppt_base = settings.report_ppt_embed_base or settings.report_embed_base
        with tool_scope(ctx, "open_fin_risk_report", tool_call_id, args):
            if valid_period:
                ctx.emit_event("report/embed-card", {
                    "card_id": uuid.uuid4().hex,
                    "report_type": "fin_risk",
                    "title": f"{period} 财务风险报告",
                    "company_id": 0,
                    "company_name": "",
                    "period": period,
                    # 不带 companyId：PPT 嵌入页回落默认企业
                    "url": (f"{ppt_base}/embed/ppt-generator"
                            f"?templateId={settings.report_tpl_fin_risk_id}"
                            f"&reportPeriod={period}"),
                    "tenant_id": _embed_tenant(),
                    "user_id": ctx.user_id,
                    "mode": "embed",
                    "version": 1,
                })
                return (f"已创建 {period} 财务风险报告的生成任务"
                        "（企业由报告系统默认确定），报告正在卡片内异步生成中，"
                        "请告知用户稍候，完成后可直接在卡片查看，无需等待")
            ctx.emit_event("report/embed-card", {
                "card_id": uuid.uuid4().hex,
                "report_type": "fin_risk",
                "title": "财务风险报告 · 选择参数",
                "company_id": 0,
                "company_name": "",
                "period": "",
                "url": "",
                "tenant_id": _embed_tenant(),
                "user_id": ctx.user_id,
                "mode": "form",
                "embed_base": ppt_base,
                "template_id": settings.report_tpl_fin_risk_id,
                "version": 1,
            })
        return ("表单卡已出。给用户的回复保持一句话以内（例如：请在上方卡片选择报告期间（年月），"
                "选完点击开始生成），不要罗列表单字段、不要复述卡片内已有的说明、不要再补充替代操作方式")

    @tool(
        "open_milestone_audit",
        description="打开里程碑审核卡：查询某企业某年度经营指标下待审核的里程碑拆分，弹出审核组件，"
        "用户在卡片内完成通过/退回（数据与动作直连 SCMS）。用户表达审核里程碑/审核拆分意图时调用；"
        "company（企业名）与 year（年份，默认当前年）从话语中提取，indicator_name 可选限定单个指标。",
    )
    def open_milestone_audit(
        company: str,
        year: int | None = None,
        indicator_name: str | None = None,
        category: str | None = "管理",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        from ..scms import client as scms_client
        from ..api.scms import _row_view

        yr = year or datetime.now().year
        args = {"company": company, "year": yr, "indicator_name": indicator_name, "category": category}
        with tool_scope(ctx, "open_milestone_audit", tool_call_id, args):
            company_id = scms_client.find_company_id(company, scms_client.category_code(category))
            if company_id is None:
                return f"未找到企业：{company}。请确认企业名称后重试"
            rules = scms_client.indicator_rules(company_id, yr, scms_client.category_code(category))
            if indicator_name:
                rules = [r for r in rules
                         if scms_client.indicator_match(str(r.get("indicatorName") or ""), indicator_name)]
            card_rules = []
            for r in rules:
                pending = scms_client.milestone_page(str(r.get("ruleId")), "0")
                if not pending:
                    continue
                card_rules.append({
                    "rule_id": str(r.get("ruleId")),
                    "indicator_name": str(r.get("indicatorName") or ""),
                    "rows": [_row_view(m) for m in pending],
                })
            if not card_rules:
                return f"{company} {yr} 年没有待审核的里程碑（可能均已审核或尚未拆分）"
            ctx.emit_event("indicator/audit-card", {
                "card_id": uuid.uuid4().hex,
                "company": company,
                "year": yr,
                "rules": card_rules,
                "version": 1,
            })
        total = sum(len(g["rows"]) for g in card_rules)
        return (f"已调出 {company} {yr} 年里程碑审核卡（{len(card_rules)} 个指标 / {total} 条待审核），"
                f"请在卡片中完成通过或退回")

    # ---------- 指标查看（剧本3：查看） ----------

    _VIEW_LABELS = [
        ("name", "指标名称"), ("indicatorType", "指标类型"), ("sourceType", "指标来源"),
        ("status", "状态"), ("indicatorProperty", "指标属性"), ("dataType", "数据类型"),
        ("dataCategory", "数据分类"), ("fillFrequency", "填写频率"),
        ("indicatorCategory", "指标分类"), ("maintainMethod", "维护类型"),
        ("description", "描述"), ("ruleContent", "考核内容"),
        ("baseScore", "基本分值"), ("maxScore", "加分最高值"), ("targetVal", "目标值"),
    ]
    _SOURCE_ZH = {"1": "人工录入", "2": "自动计算"}
    _ENUM_ZH = {
        "status": {"1": "启用", "0": "禁用"},
        "indicatorProperty": {"1": "预警", "0": "非预警"},
        "dataType": {"1": "原始数据", "2": "计算数据"},
        "dataCategory": {"1": "更新值", "2": "预填值"},
        "indicatorCategory": {"1": "考核", "2": "其他关注"},
        "maintainMethod": {"1": "公司维护", "2": "总部维护"},
        "fillFrequency": {"yearly": "每年", "monthly": "每月", "once": "单次", "unlimited": "不限"},
        "sourceType": _SOURCE_ZH,
    }

    def _view_fields(detail: dict, rule: dict) -> list[dict]:
        merged = {**detail, **{k: v for k, v in rule.items() if v not in (None, "")}}
        fields = []
        for key, label in _VIEW_LABELS:
            v = merged.get(key)
            if v in (None, ""):
                continue
            zh = _ENUM_ZH.get(key, {}).get(str(v))
            fields.append({"label": label, "value": zh or str(v)})
        return fields

    @tool(
        "open_indicator_view",
        description="打开指标查看卡：展示某企业某指标的只读详情（属性/分值/考核内容等，数据来自 SCMS）。"
        "用户要看某个指标详情/配置时调用；company、indicator_name 从话语中提取，"
        "category 可选（经营/战略/管理/约束/党建，默认管理），year 默认当前年。",
    )
    def open_indicator_view(
        company: str,
        indicator_name: str,
        year: int | None = None,
        category: str | None = "管理",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        from ..scms import client as scms_client

        yr = year or datetime.now().year
        args = {"company": company, "indicator_name": indicator_name, "year": yr, "category": category}
        with tool_scope(ctx, "open_indicator_view", tool_call_id, args):
            company_id = scms_client.find_company_id(company, scms_client.category_code(category))
            if company_id is None:
                return f"未找到企业：{company}。请确认企业名称后重试"
            rules = scms_client.indicator_rules(company_id, yr, scms_client.category_code(category))
            hits = [r for r in rules
                    if scms_client.indicator_match(str(r.get("indicatorName") or ""), indicator_name)]
            if not hits:
                names = "、".join(str(r.get("indicatorName")) for r in rules[:10])
                return f"{company} {yr} 年未找到指标「{indicator_name}」。该企业指标：{names}"
            rule = hits[0]
            rule_id = str(rule.get("ruleId"))
            try:
                detail = scms_client.indicator_detail(rule_id)
            except Exception:
                detail = {}  # 详情接口失败时退化为列表字段展示
            fields = _view_fields(detail, rule)
            ctx.emit_event("indicator/view-card", {
                "card_id": uuid.uuid4().hex,
                "company": company,
                "year": yr,
                "category": category or "管理",
                "indicator_name": str(rule.get("indicatorName") or indicator_name),
                "fields": fields,
                "version": 1,
            })
        return f"已调出 {company}「{rule.get('indicatorName')}」指标详情卡（只读），如需修改请前往业务系统"

    # ---------- 拆分进度统计（剧本3 统计/催办 + 剧本4 月度待办引导） ----------

    @tool(
        "query_milestone_stats",
        description="查询各企业指标拆分/审核进度（数据来自 SCMS）：返回每家企业的指标数、里程碑数、"
        "未完成拆分数、进度、红黄灯数，并发一张统计表格卡。适合回答'哪些企业还没拆完/待审核/进度如何'，"
        "以及月底/月初的'我这段时间要关注什么'。category 可选（经营/战略/管理/约束/党建，默认管理），"
        "year 默认当前年；结果可直接用于催办建议。",
    )
    def query_milestone_stats(
        year: int | None = None,
        category: str | None = "管理",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        from ..scms import client as scms_client

        yr = year or datetime.now().year
        args = {"year": yr, "category": category}
        with tool_scope(ctx, "query_milestone_stats", tool_call_id, args):
            stats = scms_client.milestone_stats(yr, scms_client.category_code(category))
            if not stats:
                return f"{yr} 年（{category or '管理'}指标）没有统计数据"
            companies = [{
                "name": str(s.get("orgName") or s.get("companyId")),
                "indicator_count": int(s.get("indicatorCount") or 0),
                "milestone_count": int(s.get("milestoneCount") or 0),
                "unfinished": int(s.get("unfinishedMilestoneCount") or 0),
                "progress": str(s.get("indicatorProgress") or "0"),
                "red": int(s.get("redCount") or 0),
                "yellow": int(s.get("yellowCount") or 0),
            } for s in stats]
            ctx.emit_event("indicator/stats-card", {
                "card_id": uuid.uuid4().hex,
                "year": yr,
                "category": category or "管理",
                "companies": companies,
                "version": 1,
            })
        laggards = [c for c in companies if c["unfinished"] > 0 or c["progress"] in ("0", "0%")]
        total_unfinished = sum(c["unfinished"] for c in companies)
        if laggards:
            who = "、".join(f"{c['name']}(未完成{c['unfinished']}条)" for c in laggards)
            summary = (f"统计卡已生成。{yr} 年共 {len(companies)} 家企业，未完成拆分/审核 {total_unfinished} 条；"
                       f"落后企业：{who}，可建议用户对其财务进行催办")
        else:
            summary = f"统计卡已生成。{yr} 年 {len(companies)} 家企业全部完成拆分与审核，无催办事项"
        return summary

    @tool(
        "generate_html_report",
        description="生成一份 HTML 报告（单文件网页版）：仅当用户明确要「html 报告 / 网页版报告」时调用；"
        "用户要「PPT / pptx / 幻灯片文件」（非财务风险报告）时告知通用 PPT 生成暂未开放，不要用本工具顶替。"
        "title 为报告标题；requirement 为内容要求（主题范围、章节侧重、数据来源等，可为空）。"
        "生成后在会话中出一张可内嵌预览、可新窗口打开的报告卡。"
        "严禁在正文里输出报告 HTML 本身或任何代码块/源码——报告内容只存在于卡片里，"
        "正文只需一句话告知用户报告已生成。",
    )
    def generate_html_report(
        title: str,
        requirement: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"title": title, "requirement": requirement}
        with tool_scope(ctx, "generate_html_report", tool_call_id, args) as db:
            html = html_report_svc.generate_html(title, requirement)
            row = html_report_svc.create_report(db, session_id=ctx.session_id, title=title, html=html)
            # 正文权威副本写对象存储（MinIO，未配置降级本地磁盘）；失败不阻断（读端点回退 DB 列）
            html_report_svc.store_artifact(row)
            ctx.emit_event("html-report/card", {
                "card_id": new_component_id("html-report"),
                "report_id": str(row.report_id),
                "title": title,
                "version": 1,
            })
        return (f"已生成 HTML 报告「{title}」，会话卡片内可直接预览或新窗口打开。"
                "正文只需一句话告知用户报告已生成，不要复述报告内容")

    @tool(
        "generate_fin_risk_report",
        description="基于当期真实风险填报数据生成本地 PPT 版财务风险报告（.pptx，可下载）。"
        "用户说「生成 PPT 财务风险报告 / 出一份财务风险报告 PPT」时立即调用"
        "（本工具仅限财务风险报告，通用主题 PPT 生成暂未开放），month=报告期间（YYYY-MM，可空=最近一期）。"
        "报告数字全部来自风险填报库；期间无数据时返回提示（此时告知用户需先下发/完成风险填报），"
        "严禁编造。生成后在会话中出一张可点击下载的 PPT 文件卡。",
    )
    def generate_fin_risk_report(
        month: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        from ...services import ppt_reports as ppt_report_svc

        period = (month or "").strip()
        args = {"month": period}
        with tool_scope(ctx, "generate_fin_risk_report", tool_call_id, args) as db:
            if period:
                if not re.fullmatch(r"\d{4}-\d{2}", period) or not 1 <= int(period[5:7]) <= 12:
                    return f"期间格式不合法：{period}（应为 YYYY-MM），请与用户确认后重试"
            else:
                # 未指定期间 → 取最新批次的归属期
                from sqlalchemy import select as _select

                from ...persistence.models import RiskFillBatch

                latest = db.scalar(
                    _select(RiskFillBatch.period)
                    .order_by(RiskFillBatch.created_at.desc()).limit(1)
                )
                if not latest:
                    return "当前没有任何风险填报批次，无法生成报告。请告知用户需先下发风险填报"
                period = latest
            def _llm(prompt: str) -> str:
                from .llm import chat_once

                return chat_once(prompt)

            result = ppt_report_svc.generate_fin_risk(
                db, session_id=ctx.session_id, period=period, llm_fn=_llm)
            if "error" in result:
                return result["error"] + "。请告知用户"
            row, stats, file_name = result["row"], result["stats"], result["file_name"]
            r = stats["red"]
            y = stats["yellow"]
            ctx.emit_event("file/record", {
                "file_id": str(row.report_id),
                "name": file_name,
                "file_type": "pptx",
                "url": f"/ppt-reports/{row.report_id}/download",
                "skill_key": "fin_risk_report",
                "version": 1,
            })
        return (f"已生成 {period} 财务风险报告 PPT（参报 {stats['companies']} 家，"
                f"红/黄/绿指标 {r}/{y}/{stats['green']} 项），点击会话中的文件卡即可下载。"
                "正文只需一句话告知用户报告已生成，不要复述报告数据")

    # 通用 PPT 生成工具已下线（业务要求 2026-09-24）：注释保留，恢复时取消注释并把 generate_ppt 加回返回列表
    # @tool(
    #     "generate_ppt",
    #     description="生成一份通用主题 PPT（.pptx，可下载）：用户说「生成 PPT / 做一份 PPT / PPT 格式」"
    #     "且主题不是财务风险报告时立即调用（财务风险报告 PPT 走 generate_fin_risk_report）。"
    #     "title 为 PPT 标题；requirement 为内容要求（受众、章节侧重、页数等，可为空）。"
    #     "生成后在会话中出一张可点击下载的 PPT 文件卡。"
    #     "严禁在正文里输出大纲 JSON、slides 结构或任何代码块——大纲只存在于 .pptx 文件里，"
    #     "正文只需一句话告知用户已生成。",
    # )
    # def generate_ppt(
    #     title: str,
    #     requirement: str = "",
    #     tool_call_id: Annotated[str, InjectedToolCallId] = "",
    # ) -> str:
    #     from ...services import ppt_reports as ppt_report_svc
    #
    #     args = {"title": title, "requirement": requirement}
    #     with tool_scope(ctx, "generate_ppt", tool_call_id, args) as db:
    #         def _llm(prompt: str) -> str:
    #             from .llm import chat_once
    #
    #             return chat_once(prompt)
    #
    #         row = ppt_report_svc.generate_generic(
    #             db, session_id=ctx.session_id, title=title,
    #             requirement=requirement, llm_fn=_llm)
    #         ctx.emit_event("file/record", {
    #             "file_id": str(row.report_id),
    #             "name": row.file_name, "file_type": "pptx",
    #             "url": f"/ppt-reports/{row.report_id}/download",
    #             "skill_key": "generic_ppt", "version": 1,
    #         })
    #     return (f"已生成 PPT「{title}」，点击会话中的文件卡即可下载。"
    #             "正文只需一句话告知用户 PPT 已生成，不要复述大纲内容")

    # 工具收敛（业务要求）：除报告嵌入卡外全部走 query_data 问数。
    # open_milestone_audit / open_indicator_view / query_milestone_stats 及
    # nl2sql_query / draw_chart 的定义保留在上文，恢复时加回返回列表即可
    return [query_data, open_post_report, open_fin_risk_report, generate_html_report,
            generate_fin_risk_report]
