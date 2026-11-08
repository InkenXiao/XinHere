"""platform_compensations.status 列宽 varchar(16)→varchar(24)

补偿状态机复核修正：新增 replaying / needs_manual_review 两个状态
（needs_manual_review 19 字符，原列宽 16 放不下），models.py 已同步 String(24)。

Revision ID: 0022_compensation_status_widen
Revises: 0021_user_notifications
Create Date: 2026-11-08
"""
from alembic import op

revision = "0022_compensation_status_widen"
down_revision = "0021_user_notifications"
branch_labels = None
depends_on = None

_TABLE = "platform_compensations"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {_TABLE} ALTER COLUMN status TYPE varchar(24)"
    )
    op.execute(
        f"COMMENT ON COLUMN {_TABLE}.status IS "
        f"'状态：pending=待重放 / replaying=重放中 / done=已重放 / "
        f"needs_manual_review=在途未知待人工 / failed=失败'"
    )


def downgrade() -> None:
    # 回窄前清掉放不下才装得下的长状态值
    op.execute(
        f"UPDATE {_TABLE} SET status='failed' "
        f"WHERE status IN ('replaying', 'needs_manual_review')"
    )
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN status TYPE varchar(16)")
