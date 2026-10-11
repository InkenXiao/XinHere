"""OA AI 工具（对话内闭环）：经 loader.plugin_tools 进图（manifest agent_tools=true）。

四件套：
- oa_create_task     派发 OA 任务 / 给自己记个人待办（→ oa-dispatch-confirm 卡）
- oa_submit_request  发起审批申请：请假/报销/通用审批单（→ oa-request-card 表单卡）
- oa_my_dashboard    查询我的待办/待我审批/我发起的（纯查询，无卡）
- oa_audit           审批人逐条审批（→ oa-audit-card 卡）

权限（若依 perms）：权限不足不抛错，返回中文提示让 LLM 转告用户（工具语义）；
落库动作的硬守卫在卡处理器（require_perm）。
"""
from __future__ import annotations

from typing import Annotated

from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import interrupt

from app.core.errors import AppError
from app.persistence.models import BizTodo
from app.platform.agent.tool_base import ToolCtx, new_component_id, tool_scope
from app.services import identity as identity_svc

from . import perm as perm_svc
from . import service as oa_svc


def _card(kind: str, props: dict) -> dict:
    """组件再确认卡标准 interrupt payload（平台 _interrupt_card 同构，重放安全）。"""
    return {
        "component_id": new_component_id(kind),
        "kind": kind,
        "kind_version": 1,
        "props": props,
        "interrupt_id": new_component_id("intr"),
        "version": 1,
    }


def make_tools(ctx: ToolCtx) -> list:

    @tool(
        "oa_create_task",
        description=(
            "派发一项 OA 任务，或给用户自己创建个人待办（title=任务标题；"
            "assignees=承办人名称列表，来自用户的 @ 提及或明确指定，不得编造；"
            "due=截止日期 YYYY-MM-DD 可选；note=补充说明可选；"
            "review_required=完成后是否需派发人审核，默认 false）。"
            "派给他人需派发权限；给自己记待办任何人可用。调用后弹确认卡等用户操作。"
        ),
    )
    def oa_create_task(
        title: str,
        assignees: list[str],
        due: str | None = None,
        note: str | None = None,
        review_required: bool = False,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"title": title, "assignees": assignees, "due": due,
                "note": note, "review_required": review_required}
        with tool_scope(ctx, "oa_create_task", tool_call_id, args):
            resolved = identity_svc.resolve_labels_to_users(assignees)
            if not resolved["assignees"]:
                unk = "、".join(resolved["unknown"]) or "（空）"
                return f"未能在组织目录中解析到承办人：{unk}。请让用户用 @ 明确人员后重试。"
            others = [a for a in resolved["assignees"] if str(a["user_id"]) != str(ctx.user_id)]
            if others and not perm_svc.has_perm(ctx.user_id, "oa:task:dispatch"):
                return "你当前没有 OA 任务派发权限（oa:task:dispatch），只能给自己安排个人待办。"
            rv = interrupt(_card("oa-dispatch-confirm", {
                "title": title, "note": note or "", "due": due,
                "review_required": review_required,
                "assignees": resolved["assignees"], "unknown": resolved["unknown"],
            }))  # 解析为只读，重放安全
        if rv.get("action") == "cancelled":
            return "用户取消了本次任务安排。如需调整（换人/改标题），请在用户补充后重新调用。"
        return rv.get("summary", f"已安排 OA 任务「{title}」")

    @tool(
        "oa_submit_request",
        description=(
            "发起 OA 审批申请并弹出表单卡。template_key：oa_leave 请假申请 / "
            "oa_expense 费用报销 / oa_generic 通用审批单；form_values=表单字段值字典"
            "（key 与模板字段一致，从对话上下文提取，不得编造）。审批人按模板规则自动解析。"
        ),
    )
    def oa_submit_request(
        template_key: str,
        form_values: dict,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"template_key": template_key, "form_values": form_values}
        with tool_scope(ctx, "oa_submit_request", tool_call_id, args):
            if not perm_svc.has_perm(ctx.user_id, "oa:request:submit"):
                return "你当前没有发起 OA 申请的权限（oa:request:submit）。"
            try:
                tpl = oa_svc._tpl(template_key)
                from app.services import task_templates
                task_templates.validate_payload(tpl, form_values or {})
            except AppError as e:
                return f"申请内容校验未通过：{e.message}。请按提示补全后重试。"
            approver = perm_svc.resolve_approver(
                tpl.get("approver_role") or "dept_leader", exclude_uid=ctx.user_id)
            if approver is None:
                return f"「{tpl['name']}」暂无可用审批人，请联系管理员配置。"
            applicant = perm_svc.user_by_id(ctx.user_id) or {}
            rv = interrupt(_card("oa-request-card", {
                "template_key": template_key, "template_name": tpl["name"],
                "form": tpl["form"], "values": form_values or {},
                "approver": {"user_id": str(approver["user_id"]), "name": approver["nick_name"]},
                "applicant": {"user_id": str(ctx.user_id),
                              "name": applicant.get("nick_name") or ctx.user_id},
            }))
        if rv.get("action") == "cancelled":
            return "用户取消了本次申请"
        return rv.get("summary", f"{tpl['name']}已提交")

    @tool(
        "oa_my_dashboard",
        description="查询当前用户的 OA 工作台：我的待办、待我审批、我发起的申请（数量+清单摘要）。"
                    "用户问「我有什么 OA 待办/谁等着审批/我的申请进度」时调用。",
    )
    def oa_my_dashboard(tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "oa_my_dashboard", tool_call_id, {}) as db:
            todos = oa_svc.my_todos(db, ctx.user_id)
            can_audit = perm_svc.has_perm(ctx.user_id, "oa:approval:audit")
            approvals = oa_svc.my_approvals(db, ctx.user_id) if can_audit else []
            requests = oa_svc.my_requests(db, ctx.user_id)
        zh = {"pending": "待处理", "returned": "已退回", "submitted": "待审批",
              "na_pending": "不涉及待确认", "completed": "已完成"}
        lines = []
        if todos:
            lines.append("我的待办 " + str(len(todos)) + " 条：" + "；".join(
                f"{t['title']}（{zh.get(t['status'], t['status'])}）" for t in todos[:5]))
        if can_audit:
            lines.append("待我审批 " + str(len(approvals)) + " 条：" + "；".join(
                f"{a['title']}（{a.get('applicant_name') or ''} 提交）" for a in approvals[:5]))
        lines.append("我发起的申请 " + str(len(requests)) + " 条：" + "；".join(
            f"{r['title']}（{zh.get(r['status'], r['status'])}）" for r in requests[:5]))
        if len(lines) == 1 and not todos and not requests:
            return "当前没有进行中的 OA 待办或申请。"
        return "【OA 概览】" + "。".join(lines)

    @tool(
        "oa_audit",
        description="审批人逐条审批 OA 申请（弹出审批卡：逐条 通过/退回需填意见/跳过）。"
                    "todo_id 可选，缺省列出全部待我审批的申请。需审批权限（oa:approval:audit）。",
    )
    def oa_audit(
        todo_id: str | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"todo_id": todo_id}
        with tool_scope(ctx, "oa_audit", tool_call_id, args) as db:
            if not perm_svc.has_perm(ctx.user_id, "oa:approval:audit"):
                return "你当前没有 OA 审批权限（oa:approval:audit）。"
            items = oa_svc.my_approvals(db, ctx.user_id)
            if todo_id:
                items = [i for i in items if i["todo_id"] == todo_id or i["todo_id"].startswith(todo_id)]
                if not items:
                    got = db.get(BizTodo, todo_id)
                    return (f"待办 {todo_id} 不在你待审批清单中"
                            f"（当前状态 {got.status if got else '不存在'}）。")
            if not items:
                return "当前没有待你审批的 OA 申请。"
            slim = [{k: i.get(k) for k in ("todo_id", "title", "applicant_name", "template_key",
                                           "submitted_at", "payload", "form")}
                    for i in items]
        rv = interrupt(_card("oa-audit-card", {"items": slim}))
        if rv.get("action") == "cancelled":
            return "已取消本次审批"
        return rv.get("summary", "审批完成")

    return [oa_create_task, oa_submit_request, oa_my_dashboard, oa_audit]
