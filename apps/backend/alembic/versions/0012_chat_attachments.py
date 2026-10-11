"""add chat_attachments table

Revision ID: 0007_chat_attachments
Revises: 0006_meeting_minutes
Create Date: 2026-09-11

- 新表 chat_attachments（问答附件：MinIO/本地对象存储 + 元数据与解析文本随行）
"""
from alembic import op

from app.persistence import models as pm

revision = "0012_chat_attachments"
down_revision = "0011_meeting_minutes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # 幂等建表（checkfirst=True）
    pm.ChatAttachment.__table__.create(bind, checkfirst=True)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_attachments")
