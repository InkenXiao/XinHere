"""子代理接缝 —— DSH SubagentProvider 思想的 Python 轻量移植（P4 改造）。

DSH 原理：子代理不是写死在 agent loop 里的第二个循环，而是 SubagentProvider 接缝
（Service Definition + Provider + Consumer）。实现 prepareContinuable 即「本地可续聊
子会话」（父已完成轮前缀 seed），仅实现 start 即「外部一次性执行」；目录发现、
深度限制、中断传播、subagent/start|end 事件由统一 runtime 承担。
xinhere 移植（LangGraph 单线程执行模型，本轮实现「外部一次性执行」语义）：
- SubagentProvider 协议 + 进程级注册表（同名 fail-loud）；
- 内置 general provider：本地子 create_deep_agent（独立 checkpoint thread_id），
  只读研究工具集（search_knowledge / list_companies），同线程同步执行；
- delegate_task 模型工具 = Consumer；深度上限 MAX_SUBAGENT_DEPTH 兜底，
  子代理工具集不含 delegate_task，杜绝递归失控；
- subagent/start|end 事件进父会话日志（DSH「模型可见即已记录」，Trajectory 可追溯）。
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from langchain_core.messages import AIMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.errors import GraphInterrupt
from typing_extensions import Annotated

from .tool_base import ToolCtx, tool_scope

logger = logging.getLogger(__name__)

MAX_SUBAGENT_DEPTH = 2  # 父图深度 0；delegate_task 产生深度 1 子代理；≥2 拒绝
EVENT_OUTPUT_LIMIT = 4000  # subagent/end 事件里 output 截断长度（完整结论经 tool result 回模型）


def _new_sub_checkpointer():
    """子代理独立 checkpointer 实例（一次性执行语义，内存态即可）。

    隔离边界（复核修正）：不与父图共享 PostgresSaver——子图 checkpoint 物理上
    不可能被父图 resume 加载，父图 get_state 亦不可能读到子图内部消息；
    子代理结论经返回值回传父图，无持久化需求。
    """
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


@dataclass
class SubagentRunSpec:
    provider: str
    name: str  # 模型面名称（如 research）
    prompt: str  # 子代理任务指令（主代理撰写）
    depth: int = 1  # 调用深度（父图工具=0）
    session_id: str = ""
    user_id: str | None = None
    model_key: str = ""  # 空 → main 通道
    parent_tool_names: list[str] = field(default_factory=list)  # 父图工具名全集（权限子集校验用）


@dataclass
class SubagentOutcome:
    ok: bool
    output: str = ""
    error: str = ""
    duration_ms: int = 0


class SubagentProvider(Protocol):
    """子代理提供方接缝：name 为注册键；run 同步执行并返回结果。"""

    name: str
    description: str

    def run(self, spec: SubagentRunSpec) -> SubagentOutcome: ...


_PROVIDERS: dict[str, SubagentProvider] = {}


def register_provider(provider: SubagentProvider, *, override: bool = False) -> None:
    if not override and provider.name in _PROVIDERS:
        raise RuntimeError(f"子代理 provider 重复注册: {provider.name}")
    _PROVIDERS[provider.name] = provider


def get_provider(name: str) -> SubagentProvider | None:
    return _PROVIDERS.get(name)


def provider_catalog() -> list[dict]:
    return [{"name": p.name, "description": p.description} for p in _PROVIDERS.values()]


def _emit(ctx: ToolCtx, type_: str, payload: dict) -> None:
    try:
        ctx.emit_event(type_, payload)
    except Exception:  # 事件失败不影响子代理执行（日志保底）
        logger.warning("subagent 事件写入失败 sid=%s type=%s", ctx.session_id, type_, exc_info=True)


def assert_child_tools_subset(child_tool_names: list[str], parent_tool_names: list[str]) -> None:
    """P8a 权限交集校验：子代理工具名必须是父级工具名集合的子集（委派链只收窄不扩张）。
    违反即 fail-loud——子代理装配期直接失败，绝不静默放行越权工具。"""
    parent = set(parent_tool_names or [])
    if not parent:
        return  # 父级未携带工具全集（如外部一次性执行方），无法校验时交由上层装配约束
    illegal = sorted(set(child_tool_names) - parent)
    if illegal:
        raise RuntimeError(
            f"子代理工具越权：{illegal} 不在父级工具集中（委派链只收窄不扩张）")


def run_subagent(spec: SubagentRunSpec, ctx: ToolCtx) -> SubagentOutcome:
    """统一 runtime：深度校验 → subagent/start → provider.run → subagent/end。
    OTel：subagent.run span 挂靠调用方工具 span 之下，子图 llm/tool span 再挂其下。"""
    if spec.depth >= MAX_SUBAGENT_DEPTH:
        return SubagentOutcome(ok=False, error=f"子代理嵌套深度已达上限（{MAX_SUBAGENT_DEPTH}）")
    provider = get_provider(spec.provider)
    if provider is None:
        return SubagentOutcome(ok=False, error=f"未注册的子代理类型：{spec.provider}（可用：{'、'.join(_PROVIDERS)}）")
    _emit(ctx, "subagent/start", {"name": spec.name, "provider": spec.provider, "depth": spec.depth})
    started = time.monotonic()
    from . import otel

    with otel.subagent_span(session_id=ctx.session_id, name=spec.name,
                            provider=spec.provider, depth=spec.depth):
        try:
            outcome = provider.run(spec)
        except GraphInterrupt:
            raise  # interrupt 属正常控制流，向上交给框架挂起
        except Exception as exc:
            logger.warning("子代理执行异常 sid=%s name=%s err=%r", ctx.session_id, spec.name, exc)
            outcome = SubagentOutcome(ok=False, error=f"子代理执行失败：{exc}")
    outcome.duration_ms = int((time.monotonic() - started) * 1000)
    _emit(ctx, "subagent/end", {
        "name": spec.name, "ok": outcome.ok,
        "output": (outcome.output or "")[:EVENT_OUTPUT_LIMIT],
        "error": outcome.error[:500], "duration_ms": outcome.duration_ms,
    })
    return outcome


# ---------------- 内置 general provider：本地只读研究子代理 ----------------


class GeneralSubagentProvider:
    """通用研究型子代理：独立 checkpoint、只读工具集、最终结论一次性回传。

    事件面只产生 subagent/start|end（子过程不刷屏父会话流）；子代理无组件卡与
    interrupt 类工具，输出纯文本结论。
    """

    name = "general"
    description = "通用研究型子代理：只读工具（知识库检索、企业清单），隔离执行并回传中文结论"

    def run(self, spec: SubagentRunSpec) -> SubagentOutcome:
        from deepagents import create_deep_agent

        from ...core.context import set_ctx, reset_ctx
        from ...core.config import settings
        from . import otel
        from .llm import build_model

        child_ctx = ToolCtx(
            user_id=spec.user_id or "system",
            session_id=spec.session_id,
            request_id=f"subagent-{uuid.uuid4().hex[:12]}",
            scms_token="",  # 子代理只读，不透传写凭证
            extra={"subagent_depth": spec.depth},  # 子图工具 span 的 depth 维度
        )
        child_prompt = (
            "你是 XinHere 主助手的子代理（研究执行者）。职责：就给定任务做只读调研并输出结论。\n"
            "规则：只使用提供的只读工具；不得臆造事实，工具无结果就如实说明；"
            "不要反问主代理或用户，把假设写进结论；全程中文（专有名词除外）。\n"
            "输出：先给结论要点（分条），再列依据；不超过 400 字。\n\n"
            f"任务：{spec.prompt}"
        )
        tools = self._build_readonly_tools(child_ctx)
        # P8a 权限交集：子代理工具集必须是父级真子集（委派链只收窄不扩张），越权 fail-loud
        assert_child_tools_subset([t.name for t in tools], spec.parent_tool_names)
        child_ctx.otel_parent = otel.get_current_context()  # 子图工具 span 挂靠 subagent.run
        graph = create_deep_agent(
            model=build_model(channel=(spec.model_key or "main"), user_id=spec.user_id),
            tools=tools,
            checkpointer=_new_sub_checkpointer(),  # 独立实例：物理隔离父图 checkpoint
            system_prompt=child_prompt,
        )
        thread_id = f"{spec.session_id}::sub::{uuid.uuid4().hex[:12]}"
        config = {"configurable": {"thread_id": thread_id}}
        audit = None
        final_text = ""
        try:
            from ...core.context import AuditCtx

            audit = set_ctx(AuditCtx(
                user_id=child_ctx.user_id, channel="tool",
                actor=f"{settings.main_model}+subagent:{spec.name}",
                session_id=spec.session_id or None, request_id=child_ctx.request_id,
                entry_point=f"subagent:{spec.provider}",
            ))
            for state in graph.stream({"messages": [("user", spec.prompt)]},
                                      config=config, stream_mode="values"):
                msgs = state.get("messages") or []
                for m in reversed(msgs):
                    if isinstance(m, AIMessage) and (m.content or "").strip():
                        c = m.content
                        final_text = c if isinstance(c, str) else "".join(
                            p.get("text", "") for p in c if isinstance(p, dict))
                        break
                if final_text:
                    break
        finally:
            if audit is not None:
                reset_ctx(audit)
        if not final_text.strip():
            return SubagentOutcome(ok=False, error="子代理未产出结论")
        return SubagentOutcome(ok=True, output=final_text.strip())

    def _build_readonly_tools(self, ctx: ToolCtx) -> list:
        from langchain_core.tools import tool

        from ...services import kb as kb_svc
        from ...services.common import list_companies

        @tool("search_knowledge", description="检索企业知识库（制度、口径、历史资料）。")
        def search_knowledge(query: str,
                             tool_call_id: Annotated[str, ""] = "") -> str:
            with tool_scope(ctx, "subagent.search_knowledge", tool_call_id, {"query": query}):
                hits = kb_svc.search(query)
            if not hits:
                return "未检索到相关内容"
            return "；".join(f"《{h['title']}》{h['snippet'][:120]}" for h in hits[:5])

        @tool("list_companies", description="列出全部被投企业名称。")
        def list_companies_tool(tool_call_id: Annotated[str, ""] = "") -> str:
            with tool_scope(ctx, "subagent.list_companies", tool_call_id, {}) as db:
                names = list_companies(db)
            return "、".join(names)

        return [search_knowledge, list_companies_tool]


register_provider(GeneralSubagentProvider())


# ---------------- Consumer：delegate_task 模型工具 ----------------


def subagent_tools(ctx: ToolCtx) -> list:
    """主图工具装配入口：深度未达上限时提供 delegate_task。"""
    depth = int(ctx.extra.get("subagent_depth", 0))
    if depth >= MAX_SUBAGENT_DEPTH:
        return []

    from langchain_core.tools import tool

    catalog = "；".join(f"{p['name']}（{p['description']}）" for p in provider_catalog())

    @tool("delegate_task", description=(
        "把一个独立子任务委派给子代理隔离执行（如资料调研、清单核对），"
        "子代理只读执行并回传结论，不产生组件卡。需要写操作或用户确认的任务不要委派。"
        f"可用子代理：{catalog}。"))
    def delegate_task(
        agent: str,
        task_prompt: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str:
        spec = SubagentRunSpec(
            provider=agent, name=agent, prompt=task_prompt,
            depth=depth + 1, session_id=ctx.session_id,
            user_id=ctx.user_id, model_key="",
            parent_tool_names=list(ctx.extra.get("tool_names") or []),
        )
        outcome = run_subagent(spec, ctx)
        if outcome.ok:
            return f"[子代理 {spec.name} 结论]\n{outcome.output}"
        return f"[子代理 {spec.name} 失败] {outcome.error}"

    return [delegate_task]
