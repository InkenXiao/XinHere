"""声纹识别表：user_biometric_consents / user_voiceprints / meeting_speaker_aliases

Revision ID: 0013_voiceprint
Revises: 0012_meeting_speaker_dispatch
Create Date: 2026-10-08

- 同意存证（PIPL 单独同意）+ 声纹无感采样累积与注册 + 会议内未注册说话人编号聚类
"""
from alembic import op

import app.persistence.models as m

revision = "0017_voiceprint"
down_revision = "0016_meeting_speaker_dispatch"
branch_labels = None
depends_on = None

_TABLES = (
    "user_biometric_consents",
    "user_voiceprints",
    "meeting_speaker_aliases",
)  # BusinessBase：updated_at 由 DB 触发器 set_updated_at() 维护


def upgrade() -> None:
    _copilot_columns()
    bind = op.get_bind()
    m.UserBiometricConsent.__table__.create(bind, checkfirst=True)
    m.UserVoiceprint.__table__.create(bind, checkfirst=True)
    m.MeetingSpeakerAlias.__table__.create(bind, checkfirst=True)
    # 红线 2：updated_at 由 DB 触发器维护（幂等：先 DROP 再 CREATE）
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at()
            """
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
    op.execute("DROP TABLE IF EXISTS meeting_speaker_aliases")
    op.execute("DROP TABLE IF EXISTS user_voiceprints")
    op.execute("DROP TABLE IF EXISTS user_biometric_consents")


def _copilot_columns() -> None:
    """原 0013_meeting_copilot_settings 并入：会议 Copilot 设置与并发锁字段。"""
    op.execute("ALTER TABLE meeting_records ADD COLUMN IF NOT EXISTS settings JSONB NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE meeting_records ADD COLUMN IF NOT EXISTS active_lock JSONB")
