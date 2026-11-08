"""OpenTelemetry 最小闭环（P7 前置）：LLM 调用、工具执行、子代理、补偿重放、
上下文裁剪（compaction）的 span 埋点与父子链路。

与 Langfuse（tracing.py）相互独立、互不依赖：
- Langfuse v3 SDK 内部自带 OTel provider，本模块用独立 TracerProvider（不调
  trace.set_tracer_provider，避免与任何既有全局 provider 冲突）；
- exporter：console（本地 stdout）/ otlp（本地或远端 collector）/ none；测试可注入
  InMemorySpanExporter。

父子链路约定：
- agent.run（executor 每轮根 span）→ llm.call（图线程内自然挂靠）
- agent.run → tool.execute（工具可能跑在 ToolNode 线程池，经 ToolCtx.otel_parent
  显式指定父上下文——contextvars 跨线程不传播）
- tool.execute → subagent.run（同线程同步执行，自然挂靠）→ 子图 llm.call / tool.execute

span 属性约定（验收口径）：session_id、step_index、tool_name、tool_tier、
token_usage（JSON 串）、subagent_depth；补偿重放另带 compensation_kind、
retry_count、result；裁剪 span 带 trimmed_messages、saved_tokens。

fail-soft 约定：span 创建失败自动退化为空观测（body 照常执行）；body 异常一律
原样传播（绝不在 span helper 里吞异常）；观测写字段（set_attr/set_error/set_usage）
内部自行兜底。
"""
from __future__ import annotations

import contextvars
import json
import logging
import threading
from contextlib import contextmanager

from ...core.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_provider = None
_tracer = None
_init_failed = False

# 图线程内的执行期维度（LLM span 读取）；工具线程读不到 contextvars，
# 工具/子代理 span 的维度经 ToolCtx 显式携带。
_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("otel_session_id", default="")
_step_index: contextvars.ContextVar[int] = contextvars.ContextVar("otel_step_index", default=0)
_subagent_depth: contextvars.ContextVar[int] = contextvars.ContextVar("otel_subagent_depth", default=0)


def setup_otel(*, exporter=None) -> None:
    """进程级初始化（幂等）。exporter 供测试注入（如 InMemorySpanExporter）；
    未注入时按 settings.otel_exporter 装配 console / otlp。失败 fail-soft。"""
    global _provider, _tracer, _init_failed
    if _tracer is not None or _init_failed:
        return
    with _lock:
        if _tracer is not None or _init_failed:
            return
        try:
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(resource=Resource.create({"service.name": "xinhere-agent"}))
            if exporter is not None:
                provider.add_span_processor(BatchSpanProcessor(exporter))
            else:
                mode = (settings.otel_exporter or "console").strip().lower()
                if mode == "otlp" and settings.otel_otlp_endpoint.strip():
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                    provider.add_span_processor(BatchSpanProcessor(
                        OTLPSpanExporter(endpoint=settings.otel_otlp_endpoint.strip())))
                elif mode == "console":
                    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

                    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
                # mode == "none"：仅建 provider 不挂 processor（span 不导出，开销最低）
            _provider = provider
            _tracer = provider.get_tracer("xinhere.agent")
        except Exception:
            _init_failed = True
            logger.warning("OTel 初始化失败，本进程不再尝试（检查 opentelemetry 依赖/配置）", exc_info=True)


def reset_for_tests() -> None:
    """仅供测试：清空进程级单例，允许重新注入 exporter。"""
    global _provider, _tracer, _init_failed
    with _lock:
        _provider = _tracer = None
        _init_failed = False


def tracer():
    if _tracer is None and not _init_failed:
        setup_otel()
    return _tracer


def get_current_context():
    """捕获当前 OTel 上下文（供跨线程显式指定父 span）。失败返回 None。"""
    try:
        from opentelemetry import context as otel_context

        return otel_context.get_current()
    except Exception:
        return None


def set_step_index(n: int) -> None:
    """executor 图线程在每个 graph step 完成时更新（LLM span / contextvar 维度）。"""
    _step_index.set(int(n))


def set_run_dims(session_id: str, depth: int = 0) -> None:
    """显式设置图线程 session/depth 维度（子代理 provider 用）。"""
    _session_id.set(session_id or "")
    _subagent_depth.set(int(depth))


class _NullObs:
    """未启用 OTel / span 创建失败时的空观测对象：接口与 _SpanObs 一致。"""

    def set_usage(self, usage: dict | None) -> None: ...
    def set_error(self, exc: BaseException) -> None: ...
    def set_attr(self, key: str, value) -> None: ...


class _SpanObs:
    def __init__(self, span):
        self._span = span

    def set_usage(self, usage: dict | None) -> None:
        if not usage:
            return
        try:
            self._span.set_attribute("token_usage", json.dumps(usage, ensure_ascii=False, default=str))
            for k in ("input_tokens", "output_tokens", "total_tokens"):
                v = usage.get(k)
                if isinstance(v, (int, float)):
                    self._span.set_attribute(f"llm.usage.{k}", int(v))
        except Exception:
            pass

    def set_error(self, exc: BaseException) -> None:
        try:
            self._span.record_exception(exc)
            self._span.set_status(2, type(exc).__name__)  # 2 = ERROR
        except Exception:
            pass

    def set_attr(self, key: str, value) -> None:
        try:
            self._span.set_attribute(key, value)
        except Exception:
            pass


def _start_span(name: str, attrs: dict, parent_context=None):
    """创建 span 上下文管理器；创建失败 → nullcontext（body 照常执行，fail-soft）。
    注意：进入后的 body 异常不在此层捕获——由调用方原样传播。"""
    t = tracer()
    if t is None:
        return None
    try:
        return t.start_as_current_span(name, attributes=attrs, context=parent_context)
    except Exception:
        logger.debug("OTel span %s 创建失败（忽略）", name, exc_info=True)
        return None


@contextmanager
def run_span(*, session_id: str, turn: int, user_id: str, request_id: str,
             run_id: str = "", epoch: int | None = None):
    """executor 每轮根 span；同时设置图线程内的 session 维度。

    P9：run_id / epoch 随根 span 属性落痕（epoch 落痕用于事后审计旧代次写入）。
    """
    _session_id.set(session_id or "")
    attrs = {"session_id": session_id, "turn": turn,
             "user_id": user_id, "request_id": request_id}
    if run_id:
        attrs["run_id"] = run_id
    if epoch is not None:
        attrs["epoch"] = epoch
    cm = _start_span("agent.run", attrs)
    if cm is None:
        yield _NullObs()
        return
    with cm as span:
        yield _SpanObs(span)


@contextmanager
def llm_span(*, model: str, channel: str, message_count: int, prompt_chars: int):
    """LLM 调用 span（图线程内，父 = agent.run 或 subagent.run）。"""
    cm = _start_span("llm.call", {
        "session_id": _session_id.get(),
        "step_index": _step_index.get(),
        "subagent_depth": _subagent_depth.get(),
        "llm.model": model or settings.main_model,
        "llm.channel": channel or "main",
        "llm.message_count": int(message_count),
        "llm.prompt_chars": int(prompt_chars),
    })
    if cm is None:
        yield _NullObs()
        return
    with cm as span:
        yield _SpanObs(span)


@contextmanager
def tool_span(*, session_id: str, tool_name: str, tool_tier: str, call_id: str,
              parent_context=None, step_index: int = 0, subagent_depth: int = 0):
    """工具执行 span。工具可能跑在 ToolNode 线程池：parent_context 与
    step_index/depth 由 ToolCtx 携带（图线程捕获/更新），避免跨线程丢维度。"""
    attrs = {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_tier": tool_tier,
        "step_index": int(step_index),
        "subagent_depth": int(subagent_depth),
    }
    if call_id:
        attrs["tool_call_id"] = call_id
    cm = _start_span("tool.execute", attrs, parent_context)
    if cm is None:
        yield _NullObs()
        return
    with cm as span:
        yield _SpanObs(span)


@contextmanager
def subagent_span(*, session_id: str, name: str, provider: str, depth: int):
    """子代理执行 span（父 = 调用方工具 span，同线程自然挂靠）；
    同时设置子图线程内的 depth/session 维度，子图 llm/tool span 挂靠其下。
    退出时恢复 depth——否则父图后续 llm/tool span 会误标为子代理深度。"""
    _session_id.set(session_id or "")
    prev_depth = _subagent_depth.get()
    _subagent_depth.set(int(depth))
    cm = _start_span("subagent.run", {
        "session_id": session_id, "subagent_name": name,
        "subagent_provider": provider, "subagent_depth": int(depth),
        "step_index": _step_index.get(),
    })
    try:
        if cm is None:
            yield _NullObs()
        else:
            with cm as span:
                yield _SpanObs(span)
    finally:
        _subagent_depth.set(prev_depth)


@contextmanager
def compensation_span(*, session_id: str, kind: str, retry_count: int, idem_key: str = ""):
    """补偿重放 span（守护线程根 span）：带 compensation_kind、retry_count；
    result 由调用方收尾时 set_attr 写入（done/pending/needs_manual_review/failed）。"""
    cm = _start_span("compensation.replay", {
        "session_id": session_id, "compensation_kind": kind,
        "retry_count": int(retry_count or 0), "idem_key": idem_key,
    })
    if cm is None:
        yield _NullObs()
        return
    with cm as span:
        yield _SpanObs(span)


@contextmanager
def compaction_span(*, session_id: str = "", step_index: int = -1):
    """上下文裁剪（compaction 触发点）span：仅当确实发生裁剪时才产生观测。
    session_id/step_index 缺省时取图线程上下文变量。"""
    cm = _start_span("context.compaction", {
        "session_id": session_id or _session_id.get(),
        "step_index": step_index if step_index >= 0 else _step_index.get(),
        "subagent_depth": _subagent_depth.get(),
    })
    if cm is None:
        yield _NullObs()
        return
    with cm as span:
        yield _SpanObs(span)


def shutdown_otel() -> None:
    """进程退出前 flush 批处理缓冲区；未启用/失败静默。"""
    if _provider is None:
        return
    try:
        _provider.shutdown()
    except Exception:
        logger.debug("OTel shutdown flush 失败（忽略）", exc_info=True)
