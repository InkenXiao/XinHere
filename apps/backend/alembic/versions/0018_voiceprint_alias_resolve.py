"""会议编号实名合并：meeting_speaker_aliases 加 resolved_user_id + 合并 0013 分叉 head

Revision ID: 0014_voiceprint_alias_resolve
Revises: 0013_meeting_copilot_settings, 0013_voiceprint
Create Date: 2026-10-08

- meeting_speaker_aliases 表新增 resolved_user_id（上传者注册成功后相似编号回绑实名）
- 同时合并 0013_meeting_copilot_settings / 0013_voiceprint 两个分叉 head（后者已 applied，
  前者尚未 applied，本迁移作为共同子节点，upgrade 时会先补跑前者再应用本迁移）
"""
from alembic import op

revision = "0018_voiceprint_alias_resolve"
down_revision = "0017_voiceprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE meeting_speaker_aliases ADD COLUMN IF NOT EXISTS resolved_user_id VARCHAR(64)"
    )
    op.execute(
        """
        COMMENT ON COLUMN meeting_speaker_aliases.resolved_user_id IS
        '实名合并：上传者注册成功后绑定的用户 ID（命中该编号直接显示实名）'
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE meeting_speaker_aliases DROP COLUMN IF EXISTS resolved_user_id")
