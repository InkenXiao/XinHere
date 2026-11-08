from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import text

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.context import AuditCtx, set_ctx  # noqa: E402
from app.persistence.models import PlatformSession  # noqa: E402
from app.persistence.session import SessionLocal, engine  # noqa: E402

try:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
except Exception as exc:  # 无库环境整体 skip，避免误报
    pytest.skip(f"数据库不可用（{exc}）", allow_module_level=True)


@pytest.fixture(autouse=True)
def _audit_ctx():
    """操作日志五要素来源：测试统一以 pytest 身份落痕。"""
    set_ctx(AuditCtx(user_id="pytest", channel="system", actor="pytest", entry_point="pytest"))
    yield


@pytest.fixture(autouse=True, scope="session")
def _otel_silent():
    """测试进程静默 OTel：otel_exporter 置 none，任何 span helper 触发 tracer() 兜底时
    只建 provider 不挂 processor（无导出线程、无 atexit flush）——避免 console exporter
    持有 pytest 已关闭的 stdout 在退出时报 I/O 噪音。需要 trace 断言的测试在各自模块
    fixture 里 reset 后显式注入 InMemorySpanExporter。"""
    from app.core.config import settings
    from app.platform.agent import otel

    prev = settings.otel_exporter
    settings.otel_exporter = "none"
    otel.reset_for_tests()
    otel.setup_otel()
    yield
    settings.otel_exporter = prev
    otel.shutdown_otel()
    otel.reset_for_tests()


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def mk_session(db):
    """平台会话 factory：平台表无审计字段，直接 add+commit。"""
    created: list[str] = []

    def _make() -> str:
        s = PlatformSession(
            user_id="pytest", domain="general", plugin_set=[], plugin_set_hash="pytest"
        )
        db.add(s)
        db.commit()
        created.append(str(s.session_id))
        return created[-1]

    yield _make
