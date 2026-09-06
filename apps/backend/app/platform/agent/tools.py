from __future__ import annotations

from typing import Annotated

from langchain_core.tools import InjectedToolCallId, tool

from ...services import dashboard as dashboard_svc
from ...services import kb as kb_svc
from ...services import todo as todo_svc
from ...services import xuanpu as xuanpu_svc
from ...services.common import list_companies
from .tool_base import ToolCtx, tool_scope


def _xuanpu_identity(db, user_id: str) -> str:
    """当前用户在 XuanPu 平台的身份（display_name，回退 username）"""
    from ...persistence.models import SysUser

    user = db.get(SysUser, user_id)
    return (user.display_name or user.username) if user else user_id


def _fail_msg(exc: Exception) -> str:
    return str(exc)[:200]


def _xj(data, limit: int = 4000) -> str:
    """XuanPu 返回值紧凑 JSON（网关错误串经 call_tool 变 {"raw": ...}，原样返回）"""
    import json as _json

    if isinstance(data, dict) and data.get("raw"):
        return str(data["raw"])[:limit]
    try:
        text = _json.dumps(data, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        text = str(data)
    return text if len(text) <= limit else text[:limit] + "…（截断）"


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

    @tool("xuanpu_chat", description="与 XuanPu 平台智能体对话（跨平台 AI 能力）。agent 传智能体 ID 或名称（名称先用 xuanpu_agents 查询），message 为用户消息。")
    def xuanpu_chat_tool(
        agent: str,
        message: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "xuanpu_chat", tool_call_id, {"agent": agent, "message": message}) as db:
            identity = _xuanpu_identity(db, ctx.user_id)
            try:
                agents = xuanpu_svc.list_agents(identity)
                target = None
                if agent.isdigit():
                    target = next((a for a in agents if str(a.get("id")) == agent), None)
                if target is None:
                    target = next((a for a in agents if a.get("name") == agent), None)
                if target is None and agents:
                    target = agents[0]  # 名称未命中时回退首个智能体
                if target is None:
                    return "XuanPu 平台暂无可用智能体"
                data = xuanpu_svc.agent_chat(int(target["id"]), message, identity)
                return f"【{target.get('name')}】回复：{data.get('reply') or '（空回复）'}"
            except Exception as exc:  # MCP/上游不可达 → 优雅降级，不崩 run
                return f"XuanPu 平台暂不可用：{_fail_msg(exc)}"

    @tool("xuanpu_todos", description="查询 XuanPu 平台待办（每周工作任务 + 填报任务）。owner 可选，默认当前用户。")
    def xuanpu_todos_tool(
        owner: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "xuanpu_todos", tool_call_id, {"owner": owner}) as db:
            identity = _xuanpu_identity(db, ctx.user_id)
            try:
                data = xuanpu_svc.todos(owner, identity)
            except Exception as exc:
                return f"XuanPu 平台暂不可用：{_fail_msg(exc)}"
        tasks = data.get("work_tasks") or []
        fills = data.get("fill_assignments") or []
        lines = [f"XuanPu 工作任务 {len(tasks)} 条："]
        for t in tasks[:10]:
            lines.append(f"- [{t.get('status') or '?'}] {t.get('name')}（{t.get('owner') or '未指派'}，{t.get('week_start')}~{t.get('week_end')}）")
        lines.append(f"填报任务 {len(fills)} 条：")
        for f in fills[:10]:
            lines.append(f"- {f.get('template_name') or f.get('template_id') or f.get('id')}（期限 {f.get('due_date') or f.get('deadline') or '未设置'}）")
        return "\n".join(lines)

    @tool("xuanpu_create_todo", description="在 XuanPu 平台创建每周工作任务（待办）。项目经理可指派他人；普通用户仅能为本人创建。")
    def xuanpu_create_todo_tool(
        name: str,
        owner: str = "",
        week_start: str = "",
        priority: str = "medium",
        remark: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"name": name, "owner": owner, "week_start": week_start,
                "priority": priority, "remark": remark}
        with tool_scope(ctx, "xuanpu_create_todo", tool_call_id, args) as db:
            identity = _xuanpu_identity(db, ctx.user_id)
            try:
                data = xuanpu_svc.todo_create(
                    name, identity, owner=owner, week_start=week_start,
                    priority=priority, remark=remark,
                )
                return (f"已在 XuanPu 创建任务「{data.get('name')}」"
                        f"（责任人 {data.get('owner') or identity}，{data.get('week_start')} 当周）")
            except Exception as exc:
                return f"XuanPu 创建待办失败：{_fail_msg(exc)}"

    @tool("xuanpu_dashboard", description="获取 XuanPu 平台数据看板概览：激活项目、进度任务、BUG 统计、需求统计。")
    def xuanpu_dashboard_tool(tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "xuanpu_dashboard", tool_call_id, {}) as db:
            identity = _xuanpu_identity(db, ctx.user_id)
            try:
                data = xuanpu_svc.dashboard(identity)
            except Exception as exc:
                return f"XuanPu 平台暂不可用：{_fail_msg(exc)}"
        proj = data.get("active_project") or {}
        lines = [f"XuanPu 激活项目：{proj.get('name') or '（无）'}"]
        pt = data.get("progress_tasks") or []
        lines.append(f"进度任务 {data.get('progress_tasks_total', len(pt))} 条，前 5 条：")
        for t in pt[:5]:
            lines.append(f"- [{t.get('status') or '?'}] {t.get('name')}（{t.get('owner') or '未指派'}）")
        for key, label in (("bug_stats_by_status", "BUG 分布"), ("req_stats_by_status", "需求分布")):
            stats = data.get(key) or {}
            if stats.get("total") is not None:
                groups = "、".join(
                    f"{g.get('label') or g.get('key') or g.get('value')}:{g.get('count')}"
                    for g in (stats.get("groups") or [])[:8]
                )
                lines.append(f"{label}（共 {stats.get('total')}）：{groups or '暂无'}")
        return "\n".join(lines)

    @tool("xuanpu_skills", description="列出 XuanPu 平台可用技能（工作流）。执行技能前先查询获取技能名称与说明。")
    def xuanpu_skills_tool(
        category: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "xuanpu_skills", tool_call_id, {"category": category}) as db:
            identity = _xuanpu_identity(db, ctx.user_id)
        try:
            items = xuanpu_svc.skills(identity, category=category)
        except Exception as exc:
            return f"XuanPu 平台暂不可用：{_fail_msg(exc)}"
        if isinstance(items, dict) and items.get("raw"):
            return items["raw"]
        lines = [f"XuanPu 技能 {len(items)} 个："]
        for s in items[:20]:
            lines.append(f"- [{s.get('id')}] {s.get('name')}（{s.get('category') or '未分类'}）：{(s.get('description') or '')[:60]}")
        return "\n".join(lines)

    @tool("xuanpu_skill_run", description="执行 XuanPu 平台技能（工作流），返回各步骤结果。skill 传技能 ID 或名称（先用 xuanpu_skills 查询），input_data 为 JSON 对象字符串。")
    def xuanpu_skill_run_tool(
        skill: str,
        input_data: str = "{}",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"skill": skill, "input_data": input_data}
        with tool_scope(ctx, "xuanpu_skill_run", tool_call_id, args) as db:
            identity = _xuanpu_identity(db, ctx.user_id)
        try:
            data = xuanpu_svc.skill_run(skill, input_data, identity)
        except Exception as exc:
            return f"XuanPu 技能执行失败：{_fail_msg(exc)}"
        return _xj(data)

    @tool("xuanpu_tools", description="列出 XuanPu 平台可调用的平台工具（原子能力，仅「使用中」状态），含参数 Schema。")
    def xuanpu_tools_tool(tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "xuanpu_tools", tool_call_id, {}) as db:
            identity = _xuanpu_identity(db, ctx.user_id)
        try:
            items = xuanpu_svc.platform_tools(identity)
        except Exception as exc:
            return f"XuanPu 平台暂不可用：{_fail_msg(exc)}"
        if isinstance(items, dict) and items.get("raw"):
            return items["raw"]
        lines = [f"XuanPu 平台工具 {len(items)} 个："]
        for t in items[:20]:
            lines.append(f"- [{t.get('id')}] {t.get('name')}（{(t.get('description') or '')[:60]}）")
        return "\n".join(lines)

    @tool("xuanpu_tool_run", description="调用 XuanPu 平台工具（原子能力）。tool 传工具 ID 或名称（先用 xuanpu_tools 查询），arguments 为按 parameters_schema 填写的 JSON 对象字符串。")
    def xuanpu_tool_run_tool(
        tool: str,
        arguments: str = "{}",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"tool": tool, "arguments": arguments}
        with tool_scope(ctx, "xuanpu_tool_run", tool_call_id, args) as db:
            identity = _xuanpu_identity(db, ctx.user_id)
        try:
            data = xuanpu_svc.tool_run(tool, arguments, identity)
        except Exception as exc:
            return f"XuanPu 工具调用失败：{_fail_msg(exc)}"
        return _xj(data)

    @tool("xuanpu_mcp_servers", description="列出用户在 XuanPu 平台注册的 MCP 服务及各服务工具清单（含参数 Schema）。")
    def xuanpu_mcp_servers_tool(tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "xuanpu_mcp_servers", tool_call_id, {}) as db:
            identity = _xuanpu_identity(db, ctx.user_id)
        try:
            items = xuanpu_svc.mcp_servers(identity)
        except Exception as exc:
            return f"XuanPu 平台暂不可用：{_fail_msg(exc)}"
        if isinstance(items, dict) and items.get("raw"):
            return items["raw"]
        lines = [f"已注册 MCP 服务 {len(items)} 个："]
        for s in items[:20]:
            tools_ = s.get("tools") or []
            names = "、".join(t.get("tool_name") or "" for t in tools_[:15])
            lines.append(f"- [{s.get('server_id')}] {s.get('name')}（{len(tools_)} 个工具）：{names}")
        return "\n".join(lines)

    @tool("xuanpu_mcp_call", description="调用用户注册的 MCP 服务上的工具（结果记入 XuanPu 调用日志）。server 传服务 ID 或名称，tool 传工具名，arguments 为 JSON 对象字符串（先用 xuanpu_mcp_servers 查询 Schema）。")
    def xuanpu_mcp_call_tool(
        server: str,
        tool: str,
        arguments: str = "{}",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"server": server, "tool": tool, "arguments": arguments}
        with tool_scope(ctx, "xuanpu_mcp_call", tool_call_id, args) as db:
            identity = _xuanpu_identity(db, ctx.user_id)
        try:
            data = xuanpu_svc.mcp_call(server, tool, arguments, identity)
        except Exception as exc:
            return f"MCP 调用失败：{_fail_msg(exc)}"
        return _xj(data)

    @tool("xuanpu_im_channels", description="列出用户在 XuanPu 平台配置的 IM 通道（飞书/企微/钉钉/邮箱等），含是否双向。")
    def xuanpu_im_channels_tool(tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "xuanpu_im_channels", tool_call_id, {}) as db:
            identity = _xuanpu_identity(db, ctx.user_id)
        try:
            items = xuanpu_svc.im_channels(identity)
        except Exception as exc:
            return f"XuanPu 平台暂不可用：{_fail_msg(exc)}"
        if isinstance(items, dict) and items.get("raw"):
            return items["raw"]
        lines = [f"IM 通道 {len(items)} 个："]
        for c in items[:20]:
            state = "启用" if c.get("enabled") else "停用"
            two = "，双向" if c.get("two_way") else "，单向推送"
            lines.append(f"- [{c.get('channel_id')}] {c.get('name')}（{c.get('type_name') or c.get('channel_type')}，{state}{two}）")
        return "\n".join(lines)

    @tool("xuanpu_im_send", description="通过用户的 IM 通道发送消息（飞书/企微/钉钉/邮箱/OA 等）。channel 可选（通道 ID/名称/类型），默认向全部启用通道发送。")
    def xuanpu_im_send_tool(
        message: str,
        channel: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        args = {"message": message, "channel": channel}
        with tool_scope(ctx, "xuanpu_im_send", tool_call_id, args) as db:
            identity = _xuanpu_identity(db, ctx.user_id)
        try:
            data = xuanpu_svc.im_send(message, identity, channel=channel)
        except Exception as exc:
            return f"IM 发送失败：{_fail_msg(exc)}"
        if isinstance(data, dict) and data.get("raw"):
            return data["raw"]
        results = data.get("results") or []
        ok_names = "、".join(r.get("name") or "" for r in results if r.get("ok"))
        err_lines = [f"{r.get('name')}：{r.get('error')}" for r in results if not r.get("ok")]
        if data.get("ok"):
            out = f"已通过 {data.get('sent')}/{data.get('total')} 个通道发送成功（{ok_names}）。"
        else:
            out = "发送失败。"
        if err_lines:
            out += "失败明细：" + "；".join(err_lines)
        return out

    @tool("xuanpu_im_messages", description="拉取 IM 通道近 6 天对话记录（仅企微 AI 助手双向通道支持）。channel 可选（通道 ID/名称），默认第一个企微 AI 助手通道。")
    def xuanpu_im_messages_tool(
        channel: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        with tool_scope(ctx, "xuanpu_im_messages", tool_call_id, {"channel": channel}) as db:
            identity = _xuanpu_identity(db, ctx.user_id)
        try:
            data = xuanpu_svc.im_messages(identity, channel=channel)
        except Exception as exc:
            return f"拉取 IM 消息失败：{_fail_msg(exc)}"
        if isinstance(data, dict) and data.get("raw"):
            return data["raw"]
        msgs = data.get("messages") or []
        if not msgs:
            return (data.get("note") or "近 6 天无消息")
        lines = [f"「{data.get('chat_name') or '企微会话'}」近 6 天消息 {len(msgs)} 条："]
        for m in msgs[-30:]:
            who = "我" if m.get("kind") == "user" else (m.get("sender") or "AI 助手")
            lines.append(f"[{m.get('send_time') or ''}] {who}：{(m.get('text') or '')[:100]}")
        return "\n".join(lines)

    return [search_knowledge, list_companies_tool, dispatch_generic_task,
            query_task_stats, xuanpu_chat_tool, xuanpu_todos_tool,
            xuanpu_create_todo_tool, xuanpu_dashboard_tool,
            xuanpu_skills_tool, xuanpu_skill_run_tool,
            xuanpu_tools_tool, xuanpu_tool_run_tool,
            xuanpu_mcp_servers_tool, xuanpu_mcp_call_tool,
            xuanpu_im_channels_tool, xuanpu_im_send_tool,
            xuanpu_im_messages_tool]
