from __future__ import annotations

from typing import Annotated

from langchain_core.tools import InjectedToolCallId, tool

from ...services import dashboard as dashboard_svc
from ...services import kb as kb_svc
from ...services import todo as todo_svc
from ...services.common import list_companies
from .tool_base import ToolCtx, tool_scope


def build_common_tools(ctx: ToolCtx) -> list:
    """平台通用工具：知识库检索/公司清单/通用派发/任务统计。"""

    @tool("search_knowledge", description="检索企业知识库，获取制度、口径、历史资料。query 为检索词，kb_id 可选限定知识库。")
    def search_knowledge(
        query: str,
        kb_id: str | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "search_knowledge", tool_call_id, {"query": query, "kb_id": kb_id}):
            try:
                hits = kb_svc.search(query, kb_id)
            except Exception as exc:  # MCP 不可达 → 优雅降级，不崩 run
                return f"知识库暂不可用：{exc}"
        if not hits:
            return "未检索到相关内容"
        top = "；".join(f"《{h['title']}》{h['snippet'][:80]}" for h in hits[:3])
        return f"命中 {len(hits)} 条，前 3 条：{top}"

    @tool("list_companies", description="列出全部 11 家被投企业名称。")
    def list_companies_tool(tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "list_companies", tool_call_id, {}) as db:
            companies = list_companies(db)
        return "被投企业清单（共 %d 家）：%s" % (len(companies), "、".join(companies))

    @tool("dispatch_generic_task", description="向指定用户派发一个通用待办任务。")
    def dispatch_generic_task(
        assignee_username: str,
        title: str,
        content: str,
        due: str | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"assignee_username": assignee_username, "title": title, "content": content, "due": due}
        with tool_scope(ctx, "dispatch_generic_task", tool_call_id, args) as db:
            from sqlalchemy import select

            from ...persistence.models import SysUser

            assignee = db.scalars(select(SysUser).where(SysUser.username == assignee_username)).first()
            if assignee is None:
                return f"用户不存在：{assignee_username}"
            task = todo_svc.create_task(
                db, scene="generic", title=title, dispatcher_id=ctx.user_id, payload={"content": content}
            )
            todo_svc.create_todo(
                db, task=task, assignee_id=assignee.user_id, kind="action",
                title=title, sub=f"派送人：{ctx.user_id}", ref={"content": content},
            )
        return f"已向 {assignee_username} 派发任务「{title}」"

    @tool("query_task_stats", description="查询任务执行统计（进行中任务、近 7 天完成、完成率、逾期）。")
    def query_task_stats(tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "query_task_stats", tool_call_id, {}) as db:
            s = dashboard_svc.summary(db)
        o = s["overview"]
        scenes = "、".join(f"{x['scene']}:{x['done']}/{x['total']}" for x in s["by_scene"])
        return (
            f"进行中任务 {o['open_tasks']}，近 7 天完成 {o['completed_7d']}，"
            f"完成率 {o['completion_rate']:.0%}，逾期 {o['overdue']}。分场景：{scenes or '暂无'}"
        )

    return [search_knowledge, list_companies_tool, dispatch_generic_task, query_task_stats]
