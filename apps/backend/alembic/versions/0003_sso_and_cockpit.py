"""sso and cockpit: sys_users SSO 绑定 + sys_auth_tokens XuanPu token + cockpit_entries

Revision ID: 0003_sso_and_cockpit
Revises: 0002_skill_tables
Create Date: 2026-09-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_sso_and_cockpit"
down_revision = "0002_skill_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # sys_users: 账号来源 (local=密码登录 / sso=统一身份登录) + XuanPu 用户绑定
    op.add_column(
        "sys_users",
        sa.Column("auth_source", sa.String(16), nullable=False, server_default="local"),
    )
    op.add_column("sys_users", sa.Column("xuanpu_user_id", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_sys_users_xuanpu_user_id", "sys_users", ["xuanpu_user_id"])
    # sys_auth_tokens: XuanPu 网关 token (双轨鉴权, 优先 Bearer)
    op.add_column("sys_auth_tokens", sa.Column("xuanpu_token", sa.Text(), nullable=True))
    op.add_column(
        "sys_auth_tokens",
        sa.Column("xuanpu_token_exp", sa.DateTime(timezone=True), nullable=True),
    )
    # 新表: Xin台入口配置 (配置表, 无业务审计, 直接 DDL 建表)
    op.create_table(
        "cockpit_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("entry_path", sa.Text(), nullable=False),
        sa.Column("icon", sa.Text(), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("key", name="uq_cockpit_entries_key"),
    )


def downgrade() -> None:
    op.drop_table("cockpit_entries")
    op.drop_column("sys_auth_tokens", "xuanpu_token_exp")
    op.drop_column("sys_auth_tokens", "xuanpu_token")
    op.drop_constraint("uq_sys_users_xuanpu_user_id", "sys_users", type_="unique")
    op.drop_column("sys_users", "xuanpu_user_id")
    op.drop_column("sys_users", "auth_source")
