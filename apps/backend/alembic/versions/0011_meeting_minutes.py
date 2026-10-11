"""add meeting_records and meeting_segments tables

Revision ID: 0006_meeting_minutes
Revises: 0005_dynamic_template
Create Date: 2026-09-08

- 新表 meeting_records（会议主表，按人/时间归档至 MinIO，记录状态与 AI 分析成果）
- 新表 meeting_segments（音频切片与 ASR 转写结果）
"""
from alembic import op

from app.modules.meeting import models as m

revision = "0011_meeting_minutes"
down_revision = "0010_dynamic_template"
branch_labels = None
depends_on = None

_TABLES = ("meeting_records", "meeting_segments")


def upgrade() -> None:
    bind = op.get_bind()

    # 1) 两张新表（幂等：checkfirst=True）
    for cls in (m.MeetingRecord, m.MeetingSegment):
        cls.__table__.create(bind, checkfirst=True)

    # 兼容表已存在的情况，确保 user_notes 字段存在
    op.execute("ALTER TABLE meeting_records ADD COLUMN IF NOT EXISTS user_notes JSONB NOT NULL DEFAULT '[]'")

    # 2) 注册 updated_at 自动更新触发器（使用数据库全局 set_updated_at 函数）
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at "
            f"BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )

    # 3) 看门狗字段（原 0008_meeting_watchdog 并入）
    op.execute("ALTER TABLE meeting_records ADD COLUMN IF NOT EXISTS end_reason VARCHAR(64)")
    op.execute("ALTER TABLE meeting_records ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ DEFAULT NOW()")


def downgrade() -> None:
    op.execute("ALTER TABLE meeting_records DROP COLUMN IF EXISTS last_active_at")
    op.execute("ALTER TABLE meeting_records DROP COLUMN IF EXISTS end_reason")
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
