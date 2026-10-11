"""会议说话人编号唯一约束：防并发聚类重复建号

Revision ID: 0015_alias_unique_label
Revises: 0014_voiceprint_alias_resolve
Create Date: 2026-10-10

- meeting_speaker_aliases 加 (meeting_id, speaker_label) 唯一约束
- 先清理存量重复行（保留每组 id 最小的一条），再建约束
- 应用层 match_or_create_alias 撞约束时回落重查已有编号行
"""
from alembic import op
from sqlalchemy import text

revision = "0019_alias_unique_label"
down_revision = "0018_voiceprint_alias_resolve"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 存量去重：同一 (meeting_id, speaker_label) 保留 id 最小的一条
    op.execute(
        """
        DELETE FROM meeting_speaker_aliases a
        USING meeting_speaker_aliases b
        WHERE a.meeting_id = b.meeting_id
          AND a.speaker_label = b.speaker_label
          AND a.id > b.id
        """
    )
    # 幂等：模型建表路径（0017）可能已带同名约束，重复执行不炸
    exists = op.get_bind().execute(text(
        """
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_meeting_alias_mtg_label'
          AND conrelid = 'meeting_speaker_aliases'::regclass
        """
    )).first()
    if not exists:
        op.create_unique_constraint(
            "uq_meeting_alias_mtg_label",
            "meeting_speaker_aliases",
            ["meeting_id", "speaker_label"],
        )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE meeting_speaker_aliases "
        "DROP CONSTRAINT IF EXISTS uq_meeting_alias_mtg_label"
    )
