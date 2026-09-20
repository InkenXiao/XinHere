from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psycopg
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from sqlalchemy import func, select

from ...core.config import settings
from ...persistence.models import (
    PlatformProjection,
    PlatformSession,
    PlatformSessionEvent,
    SysUser,
)
from ...persistence.session import SessionLocal
from ..events.store import store
from .event_translator import EventTranslator
from .llm import build_model
from .stream_bridge import bridge
from .tool_base import ToolCtx

SYSTEM_PROMPT = """你是 XinHere（新在这里，心在这里）的财务智能助手，服务本部财务与被投企业财务。
你可以：检索知识库、发起风险预警财务指标填报、现金保障倍数填报、经营者考核填报、
里程碑反馈、亮灯调整、生成投后报告、派发通用任务、查询任务执行统计。
你还可以联动 XuanPu 平台：与平台智能体对话（xuanpu_chat）、查建待办（xuanpu_todos / xuanpu_create_todo）、
看数据看板（xuanpu_dashboard）、执行平台技能（xuanpu_skills → xuanpu_skill_run；
上架技能 xuanpu_skill_info 读执行指引并严格照做，指引中的包内脚本用 xuanpu_skill_script 执行）、
调用平台工具（xuanpu_tools → xuanpu_tool_run）、调用用户注册的 MCP 服务（xuanpu_mcp_servers → xuanpu_mcp_call）、
收发 IM 消息（xuanpu_im_send 发送 / xuanpu_im_messages 拉取企微对话记录）、
发起与处理填报任务（xuanpu_fill_create 发起 / xuanpu_fill_my 查我的待填 / xuanpu_fill_submit 填写提交）、
检索平台接入的知识库（xuanpu_kb_search）。
联动前若不确定名称，先调对应列表工具查询，不要臆造名称。
规则：涉及数据填报/下发的动作必须先调用对应工具；不要臆造企业名称，先用 list_companies 确认；
归属期用 YYYY-MM 或用户给定的自然期间。回答用中文，简明专业。"""

logger = logging.getLogger(__name__)


@dataclass
class RunHandle:
    session_id: str
    turn: int
    user_id: str
    request_id: str
    model_key: str = ""  # 模型通道（run 期重建图/resume 时保持一致）
    skill_prompt: str | None = None  # 技能执行指引（本轮对话注入系统提示词尾部）
    skill_key: str = ""  # 本地文件夹技能 key（本轮动态装配包内文件/脚本工具）
    skill_source: str = ""  # 技能来源：local（本地技能）/ xuanpu（市场技能，skill_key 为同名本地包回退）
    status: str = "running"  # running / waiting_interrupt / done
    cancel_event: threading.Event = field(default_factory=threading.Event)
    pending_interrupt_id: str | None = None
    pending_component_id: str | None = None


class AgentExecutor:
    """后台线程驱动 graph.stream；sync checkpointer 全程不碰事件循环。"""

    def __init__(self) -> None:
        self._runs: dict[str, RunHandle] = {}
        self._lock = threading.Lock()
        self._saver = None
        self._cp_lock = threading.Lock()  # psycopg 连接非线程安全，图执行串行化
        self._ready = threading.Event()

    # ---------- 装配 ----------

    def setup(self) -> None:
        """启动时调用（线程内）：建 checkpoint 连接与表。"""
        if self._saver is not None:
            return
        from langgraph.checkpoint.postgres import PostgresSaver

        conn = psycopg.connect(settings.checkpoint_url, autocommit=True, prepare_threshold=0)
        self._saver = PostgresSaver(conn)
        self._saver.setup()
        self._ready.set()

    # 技能轮次输出呈现约定（前端对话流已支持 Markdown 渲染，与技能流程的断点/交付规范配套）
    SKILL_OUTPUT_STYLE = (
        "输出呈现约定（前端支持 Markdown 渲染）："
        "① 过程可见：每个阶段开始先输出一行阶段标题（如「**S0: 调研范围确认**」），"
        "每批工具调用前用一句话说明本步目的，不要连续静默调用多个工具；"
        "② 结构化表达：确认表、核对清单、对比结论一律用 Markdown 表格/列表呈现，关键结论加粗；"
        "③ 最终汇报：交付物用表格列出（文件、说明），随后给出执行摘要与质量/缺口说明。"
    )

    def _build_graph(self, tool_ctx: ToolCtx, model_key: str = "", skill_prompt: str | None = None,
                     skill_key: str = "", skill_source: str = ""):
        from deepagents import create_deep_agent

        from ..plugins.loader import plugin_tools
        from .tools import build_common_tools

        tools = build_common_tools(tool_ctx) + plugin_tools(tool_ctx)
        # 技能轮次：指引注入系统提示词尾部（对基础路由规则的补充约定）
        skill_note = ""
        if skill_key and skill_source == "xuanpu":
            # 市场技能 + 本地同名包回退：指引来自技能市场，文件/脚本工具由本地同名包提供，
            # 使市场链路具备真实落盘（.work/ 暂存、reports/ 产出）与 PDF 生成能力
            from .skill_tools import build_skill_tools

            tools = tools + build_skill_tools(tool_ctx, skill_key)
            skill_note = (
                f"\n\n# 当前技能约定（技能市场技能，本地同名技能包 {skill_key} 提供文件工具）\n{skill_prompt}\n\n"
                "技能约定补充：本技能的文件读写与脚本执行一律使用本地技能包三工具——"
                "读取包内参考文件用 read_skill_file；执行包内脚本（如 orchestrator.sh）用 run_skill_script"
                "（script 传包内相对路径，arguments 传参数数组）；写暂存文件用 write_skill_file"
                "（长文组装：先写第一块，再以 append=true 逐块追加）。"
                "不要调用 xuanpu_skill_script / xuanpu_skill_run，脚本一律经 run_skill_script 在本地执行，"
                "产物（报告 MD 与 PDF）落在本地技能包内并真实落盘。"
                "必须真实调用工具获取结果，不得凭空假设脚本内容或输出。"
                "指引要求向用户确认或补充信息时，先输出问题等待用户回复，再继续推进。"
                "技能约定不改变输出语言：无论指引、脚本输出为何种语言，对用户的一切输出必须是中文"
                "（专有名词、代码、命令、路径除外）。"
                + self.SKILL_OUTPUT_STYLE
            )
        elif skill_key:
            # 本地文件夹技能：动态装配包内文件/脚本工具，指引中的包内脚本必须真实执行
            from .skill_tools import build_skill_tools

            tools = tools + build_skill_tools(tool_ctx, skill_key)
            skill_note = (
                f"\n\n# 当前技能约定（本地技能 {skill_key}）\n{skill_prompt}\n\n"
                "技能约定补充：指引中要求执行包内脚本（如 orchestrator.sh）时，"
                "必须调用 run_skill_script 工具实际执行（script 传指引中的包内相对路径，"
                "arguments 传参数数组）；要求读取包内参考文件时用 read_skill_file；"
                "需要写暂存文件时用 write_skill_file。"
                "必须真实调用工具获取结果，不得凭空假设脚本内容或输出。"
                "指引要求向用户确认或补充信息时，先输出问题等待用户回复，再继续推进。"
                "技能约定不改变输出语言：无论指引、脚本输出为何种语言，对用户的一切输出必须是中文"
                "（专有名词、代码、命令、路径除外）。"
                + self.SKILL_OUTPUT_STYLE
            )
        elif skill_prompt:
            # 技能市场技能：指引中的包内脚本必须真调 xuanpu_skill_script 执行
            skill_note = (
                f"\n\n# 当前技能约定\n{skill_prompt}\n\n"
                "技能约定补充：指引中要求执行包内脚本（如 orchestrator.sh）时，"
                "必须调用 xuanpu_skill_script 工具实际执行（skill 传技能名，script 传指引中的包内相对路径，"
                "arguments 传参数数组的 JSON 字符串）；要求发起技能运行时用 xuanpu_skill_run。"
                "必须真实调用工具获取结果，不得凭空假设脚本内容或输出。"
                "指引要求向用户确认或补充信息时，先输出问题等待用户回复，再继续推进。"
                "技能约定不改变输出语言：无论指引、脚本输出为何种语言，对用户的一切输出必须是中文"
                "（专有名词、代码、命令、路径除外）。"
                + self.SKILL_OUTPUT_STYLE
            )
        return create_deep_agent(
            model=build_model(channel=(model_key or "").strip().lower() or "main"),
            tools=tools,
            checkpointer=self._saver,
            system_prompt=SYSTEM_PROMPT + skill_note,
        )

    # ---------- run 生命周期 ----------

    def get_run(self, session_id: str) -> RunHandle | None:
        with self._lock:
            h = self._runs.get(session_id)
            if h and h.status == "done":
                return None
            return h

    def start_turn(self, session_id: str, user: SysUser, content: str, request_id: str,
                   model_key: str = "", skill_prompt: str | None = None, skill_key: str = "",
                   skill_source: str = "") -> int:
        """登记 run 并起后台线程；返回 turn 号。调用方需先落 user/message 与 turn/start。"""
        with SessionLocal() as db:
            turn = db.scalar(
                select(func.count()).select_from(PlatformSessionEvent).where(
                    PlatformSessionEvent.session_id == uuid.UUID(session_id),
                    PlatformSessionEvent.type == "turn/start",
                )
            )
        handle = RunHandle(session_id=session_id, turn=turn, user_id=user.user_id, request_id=request_id,
                           model_key=model_key, skill_prompt=skill_prompt, skill_key=skill_key,
                           skill_source=skill_source)
        with self._lock:
            self._runs[session_id] = handle
        logger.info("run 启动 sid=%s turn=%d user=%s req=%s skill=%s(%s)",
                    session_id, turn, user.username, request_id,
                    "已注入" if skill_prompt else "-", skill_key or "-")
        threading.Thread(
            target=self._run, args=(handle, content, None), name=f"run-{session_id[:8]}-t{turn}", daemon=True
        ).start()
        return turn

    def resume(self, session_id: str, resume_value: dict) -> None:
        handle = self.get_run(session_id)
        if handle is None or handle.status != "waiting_interrupt":
            logger.info("resume 忽略：无 waiting run sid=%s", session_id)
            return
        handle.status = "running"
        handle.pending_interrupt_id = None
        logger.info("run resume sid=%s turn=%d action=%s",
                    session_id, handle.turn, resume_value.get("action"))
        threading.Thread(
            target=self._run, args=(handle, None, resume_value),
            name=f"run-{session_id[:8]}-resume", daemon=True,
        ).start()

    def resume_detached(self, session_id: str, user: SysUser, request_id: str, resume_value: dict) -> None:
        """进程重启后的孤儿 interrupt：凭 PG checkpoint 恢复 run。"""
        with SessionLocal() as db:
            turn = db.scalar(
                select(func.coalesce(func.max(PlatformSessionEvent.turn), 1)).where(
                    PlatformSessionEvent.session_id == uuid.UUID(session_id)
                )
            )
        handle = RunHandle(session_id=session_id, turn=int(turn or 1),
                           user_id=user.user_id, request_id=request_id, status="running")
        with self._lock:
            self._runs[session_id] = handle
        logger.info("run resume_detached sid=%s turn=%d user=%s", session_id, handle.turn, user.username)
        threading.Thread(
            target=self._run, args=(handle, None, resume_value),
            name=f"run-{session_id[:8]}-resume-detached", daemon=True,
        ).start()

    def cancel(self, session_id: str) -> str:
        handle = self.get_run(session_id)
        if handle is None:
            return "idle"
        if handle.status == "waiting_interrupt":
            return "waiting"  # 走 components cancel 路径
        handle.cancel_event.set()
        return "cancelling"

    # ---------- 后台执行 ----------

    def _run(self, handle: RunHandle, content: str | None, resume_value: dict | None) -> None:
        sid = handle.session_id

        def emit(type_: str, payload: dict):
            return store.append(sid, type_, payload, turn=handle.turn)

        tool_ctx = ToolCtx(
            user_id=handle.user_id, session_id=sid, request_id=handle.request_id,
            turn=handle.turn, emit=emit,
        )
        translator = EventTranslator(store, sid, handle.turn)
        try:
            self._ready.wait(timeout=30)
            graph = self._build_graph(tool_ctx, handle.model_key, handle.skill_prompt,
                                      handle.skill_key, handle.skill_source)
            config = {"configurable": {"thread_id": sid}}
            input_ = (
                Command(resume=resume_value)
                if resume_value is not None
                else {"messages": [HumanMessage(content=content)]}
            )
            with self._cp_lock:
                stream = graph.stream(
                    input_, config, stream_mode=["messages", "updates"], subgraphs=False
                )
                try:
                    for item in stream:
                        if handle.cancel_event.is_set():
                            stream.close()
                            break
                        mode, payload = item
                        if mode == "messages":
                            chunk, meta = payload
                            translator.handle_chunk(chunk, meta)
                        elif mode == "updates":
                            translator.handle_update(payload)
                finally:
                    translator.finalize()
                if handle.cancel_event.is_set():
                    logger.info("run 已中止 sid=%s turn=%d", sid, handle.turn)
                    store.append(sid, "turn/end", {"turn": handle.turn, "reason": "aborted", "version": 1}, turn=handle.turn)
                    self._finish(handle, "done")
                    return
                state = graph.get_state(config)

            interrupts = []
            for task in state.tasks or []:
                interrupts.extend(getattr(task, "interrupts", None) or [])

            if interrupts:
                intr = interrupts[0]
                payload = dict(intr.value)
                payload.setdefault("interrupt_id", uuid.uuid4().hex)
                store.append(sid, "component/request", payload, turn=handle.turn)
                handle.status = "waiting_interrupt"
                handle.pending_interrupt_id = payload["interrupt_id"]
                handle.pending_component_id = payload.get("component_id")
                logger.info("run 挂起等待 interrupt sid=%s turn=%d cid=%s intr=%s kind=%s",
                            sid, handle.turn, handle.pending_component_id,
                            payload["interrupt_id"], payload.get("kind"))
                bridge.publish_marker(sid, "waiting_interrupt")
                return

            store.append(sid, "turn/end", {"turn": handle.turn, "reason": "completed", "version": 1}, turn=handle.turn)
            logger.info("run 完成 sid=%s turn=%d", sid, handle.turn)
            self._update_stats_projection(sid)
            self._finish(handle, "done")
        except Exception as exc:
            logger.exception("run 异常 sid=%s turn=%d err=%r", sid, handle.turn, exc)
            try:
                translator.finalize()
                bridge.publish(sid, {"type": "error", "id": f"{sid}:-1",
                                     "data": {"seq": -1, "time": datetime.now().isoformat(),
                                              "code": "INTERNAL", "message": str(exc)[:300]}})
                store.append(sid, "turn/end", {"turn": handle.turn, "reason": "error", "version": 1}, turn=handle.turn)
            except Exception:
                pass
            self._finish(handle, "done")

    def _finish(self, handle: RunHandle, status: str) -> None:
        handle.status = status
        bridge.publish_marker(handle.session_id, status)
        if status == "done":
            with self._lock:
                self._runs.pop(handle.session_id, None)

    def _update_stats_projection(self, session_id: str) -> None:
        sid = uuid.UUID(session_id)
        with SessionLocal() as db:
            turns = db.scalar(
                select(func.count()).select_from(PlatformSessionEvent).where(
                    PlatformSessionEvent.session_id == sid, PlatformSessionEvent.type == "turn/end"
                )
            )
            msgs = db.scalar(
                select(func.count()).select_from(PlatformSessionEvent).where(
                    PlatformSessionEvent.session_id == sid, PlatformSessionEvent.type == "assistant/message"
                )
            )
            max_seq = db.scalar(
                select(func.coalesce(func.max(PlatformSessionEvent.seq), -1)).where(
                    PlatformSessionEvent.session_id == sid
                )
            )
            row = db.get(PlatformProjection, (sid, "stats"))
            value = {"turns": int(turns or 0), "assistant_messages": int(msgs or 0)}
            if row is None:
                db.add(PlatformProjection(session_id=sid, key="stats", state_version=1, seq=max_seq, value=value))
            else:
                row.seq = max_seq
                row.value = value
            db.commit()
        bridge.publish(sid_hex(session_id), {"type": "projection", "id": f"{session_id}:{max_seq}",
                                             "data": {"seq": max_seq, "time": datetime.now().isoformat(),
                                                      "key": "stats", "value": value}})

    # ---------- 崩溃恢复 ----------

    def recover_crashed(self) -> int:
        """启动扫描：补 turn/end{reason:'crashed'} 与未闭合工具 tool/result{outcome:'unknown'}。"""
        recovered = 0
        since = datetime.now() - timedelta(days=7)
        with SessionLocal() as db:
            rows = db.execute(
                select(PlatformSessionEvent.session_id, PlatformSessionEvent.seq, PlatformSessionEvent.type,
                       PlatformSessionEvent.data)
                .where(PlatformSessionEvent.time >= since)
                .order_by(PlatformSessionEvent.session_id, PlatformSessionEvent.seq)
            ).all()
        by_session: dict[str, list] = {}
        for r in rows:
            by_session.setdefault(str(r[0]), []).append(r)
        for sid, evts in by_session.items():
            open_turns: dict[int, bool] = {}
            open_calls: dict[str, tuple[int, str]] = {}
            for _sid, seq, type_, data in evts:
                if type_ == "turn/start":
                    open_turns[data.get("turn", 0)] = True
                elif type_ == "turn/end":
                    open_turns.pop(data.get("turn", 0), None)
                elif type_ == "tool/call":
                    open_calls[data.get("call_id", "")] = (seq, data.get("name", ""))
                elif type_ == "tool/result":
                    open_calls.pop(data.get("call_id", ""), None)
            for turn in sorted(open_turns):
                for call_id, (call_seq, name) in open_calls.items():
                    store.append(sid, "tool/result",
                                 {"call_id": call_id, "name": name, "content": "", "is_error": True,
                                  "outcome": "unknown", "refs": [call_seq], "version": 1}, turn=turn)
                store.append(sid, "turn/end", {"turn": turn, "reason": "crashed", "version": 1}, turn=turn)
                recovered += 1
        if recovered:
            logger.warning("崩溃恢复：补 %d 个未完成 turn（crashed 收尾 + tool/result unknown）", recovered)
        else:
            logger.info("崩溃恢复：无未完成 turn")
        return recovered


def sid_hex(session_id: str) -> str:
    return session_id


executor = AgentExecutor()
