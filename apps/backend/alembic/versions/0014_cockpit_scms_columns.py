"""cockpit 表并入 scms 侧语义列（surface / perm_key / action / params…）

原 scms 0010_cockpit_config 建 4 表；src 侧 0003/0004 已建 cockpit_entries/groups/cards
+ hero_cards（kind/subtitle/soon 语义）。本迁移不重建表，只对既有表补齐 scms 侧列：
- cockpit_groups.surface（dashboard=驾驶舱首页 / xin_tai=Xin台；存量数据归 xin_tai）
- cockpit_cards.{description,pending,action,params,perm_key}（scms 侧 action/params 渲染体系）

Revision ID: 0014_cockpit_scms_columns
Revises: 0013_html_reports
Create Date: 2026-10-11
"""
from alembic import op

revision = "0014_cockpit_scms_columns"
down_revision = "0013_html_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 分组归属壳屏（存量分组归 Xin台；驾驶舱分组由 seed 显式建 surface=dashboard）
    op.execute(
        "ALTER TABLE cockpit_groups ADD COLUMN IF NOT EXISTS surface VARCHAR(16) "
        "NOT NULL DEFAULT 'xin_tai'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cockpit_groups_surface ON cockpit_groups (surface, enabled)"
    )
    # 卡片 scms 侧语义列（与 kind/subtitle/soon 并存，两套前端各取所需）
    op.execute("ALTER TABLE cockpit_cards ADD COLUMN IF NOT EXISTS description TEXT")
    op.execute(
        "ALTER TABLE cockpit_cards ADD COLUMN IF NOT EXISTS pending BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE cockpit_cards ADD COLUMN IF NOT EXISTS action VARCHAR(16) "
        "NOT NULL DEFAULT 'sso_scms'"
    )
    op.execute(
        "ALTER TABLE cockpit_cards ADD COLUMN IF NOT EXISTS params JSONB NOT NULL DEFAULT '{}'"
    )
    op.execute("ALTER TABLE cockpit_cards ADD COLUMN IF NOT EXISTS perm_key TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE cockpit_cards DROP COLUMN IF EXISTS perm_key")
    op.execute("ALTER TABLE cockpit_cards DROP COLUMN IF EXISTS params")
    op.execute("ALTER TABLE cockpit_cards DROP COLUMN IF EXISTS action")
    op.execute("ALTER TABLE cockpit_cards DROP COLUMN IF EXISTS pending")
    op.execute("ALTER TABLE cockpit_cards DROP COLUMN IF EXISTS description")
    op.execute("DROP INDEX IF EXISTS ix_cockpit_groups_surface")
    op.execute("ALTER TABLE cockpit_groups DROP COLUMN IF EXISTS surface")
