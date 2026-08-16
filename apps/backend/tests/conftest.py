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
