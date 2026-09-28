"""user model prefs: sys_user_model_prefs 用户个人模型偏好表

按模型通道 (main/small/coder/vision) 保存用户级覆盖；空字段 / 0 回退 .docker.env 系统默认。
Revision ID: 0006_user_model_prefs
Revises: 0005_db_chinese_comments
Create Date: 2026-09-27
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_user_model_prefs"
down_revision = "0005_db_chinese_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sys_user_model_prefs",
        sa.Column("pref_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("api_url", sa.String(512), nullable=False, server_default=""),
        sa.Column("api_key", sa.String(256), nullable=False, server_default=""),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_delete", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "channel", name="uq_user_model_pref"),
        comment="用户个人模型偏好（main/small/coder/vision 通道级覆盖；未设置的通道走 .docker.env 默认）",
    )
    op.create_index("idx_ump_user", "sys_user_model_prefs", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_ump_user", table_name="sys_user_model_prefs")
    op.drop_table("sys_user_model_prefs")
