"""add speaker and dispatch fields to meeting tables

Revision ID: 0012_meeting_speaker_dispatch
Revises: 0011_ppt_reports
Create Date: 2026-09-22

- meeting_records 表新增 speakers JSONB DEFAULT '[]'
- meeting_segments 表新增 speaker_id VARCHAR(32)、speaker_name VARCHAR(64) DEFAULT '员工'
"""
from alembic import op

revision = "0016_meeting_speaker_dispatch"
down_revision = "0015_ppt_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE meeting_records ADD COLUMN IF NOT EXISTS speakers JSONB NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE meeting_segments ADD COLUMN IF NOT EXISTS speaker_id VARCHAR(32)")
    op.execute("ALTER TABLE meeting_segments ADD COLUMN IF NOT EXISTS speaker_name VARCHAR(64) NOT NULL DEFAULT '员工'")


def downgrade() -> None:
    op.execute("ALTER TABLE meeting_records DROP COLUMN IF EXISTS speakers")
    op.execute("ALTER TABLE meeting_segments DROP COLUMN IF EXISTS speaker_id")
    op.execute("ALTER TABLE meeting_segments DROP COLUMN IF EXISTS speaker_name")
