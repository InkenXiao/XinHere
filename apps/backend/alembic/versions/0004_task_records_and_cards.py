"""历史任务 + Xin台分组卡片 + Xin语自定义卡片

- cockpit_groups / cockpit_cards：Xin台分组卡片配置（配置表，硬删）
- task_records：历史任务统一树（chat 对话 / exec 执行记录 / folder 文件夹，逻辑删除）
- hero_cards：Xin语页用户自定义卡片（逻辑删除）

Revision ID: 0004_task_records_and_cards
Revises: 0003_sso_and_cockpit
Create Date: 2026-09-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_task_records_and_cards"
down_revision = "0003_sso_and_cockpit"
branch_labels = None
depends_on = None

_BUSINESS_TABLES = ("task_records", "hero_cards")


def _trigger(table: str) -> None:
    op.execute(
        f"""
        CREATE TRIGGER trg_{table}_updated_at
        BEFORE UPDATE ON {table}
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )


def upgrade() -> None:
    # Xin台卡片分组（配置表）
    op.create_table(
        "cockpit_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("key", name="uq_cockpit_groups_key"),
    )
    # Xin台卡片（配置表）
    op.create_table(
        "cockpit_cards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("group_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="task"),
        sa.Column("link_url", sa.Text(), nullable=True),
        sa.Column("skill_id", sa.Integer(), nullable=True),
        sa.Column("skill_name", sa.Text(), nullable=True),
        sa.Column("icon", sa.Text(), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("key", name="uq_cockpit_cards_key"),
    )
    op.create_index("idx_cockpit_cards_group", "cockpit_cards", ["group_key", "sort"])
    # 历史任务统一树（业务表）
    op.create_table(
        "task_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False, server_default="chat"),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default=""),
        sa.Column("ref_id", sa.String(64), nullable=True),
        sa.Column("detail", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_delete", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_task_records_user", "task_records", ["user_id", "scope", "kind"])
    op.create_index("idx_task_records_ref", "task_records", ["ref_id"])
    # Xin语页用户自定义卡片（业务表）
    op.create_table(
        "hero_cards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="link"),
        sa.Column("link_url", sa.Text(), nullable=True),
        sa.Column("skill_id", sa.Integer(), nullable=True),
        sa.Column("skill_name", sa.Text(), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_delete", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_hero_cards_user", "hero_cards", ["user_id", "is_delete"])
    for table in _BUSINESS_TABLES:
        _trigger(table)


def downgrade() -> None:
    for table in _BUSINESS_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
    op.drop_table("hero_cards")
    op.drop_table("task_records")
    op.drop_index("idx_cockpit_cards_group", table_name="cockpit_cards")
    op.drop_table("cockpit_cards")
    op.drop_table("cockpit_groups")
