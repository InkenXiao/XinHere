"""user_notifications 表（消息中心）

scms 侧 f540c0a 只改了 models 未落迁移；按 UserNotification 模型补齐建表迁移。

Revision ID: 0021_user_notifications
Revises: 0020_audit_scrub_biometric
Create Date: 2026-10-11
"""
from alembic import op

import app.persistence.models as m

revision = "0021_user_notifications"
down_revision = "0020_audit_scrub_biometric"
branch_labels = None
depends_on = None

_TABLE = "user_notifications"


def upgrade() -> None:
    bind = op.get_bind()
    m.UserNotification.__table__.create(bind, checkfirst=True)
    # 红线 2：updated_at 由 DB 触发器维护（set_updated_at 函数由 0001 创建）
    op.execute(f"DROP TRIGGER IF EXISTS trg_{_TABLE}_updated_at ON {_TABLE}")
    op.execute(
        f"CREATE TRIGGER trg_{_TABLE}_updated_at "
        f"BEFORE UPDATE ON {_TABLE} FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_{_TABLE}_updated_at ON {_TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
