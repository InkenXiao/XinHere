"""cockpit cards: subtitle 副标题 + soon 置灰占位 + kind 扩展 placeholder

Xin台卡片布局升级（智能体应用/AI促研/AI工具/业务系统四组）：卡片增加副标题行、
二期占位置灰标记；kind 新增 placeholder 功能占位类型。旧分组数据由管理端或
seed 重建，不在迁移内强改。
Revision ID: 0007_cockpit_card_subtitle_soon
Revises: 0006_user_model_prefs
Create Date: 2026-10-10
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_cockpit_card_subtitle_soon"
down_revision = "0006_user_model_prefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cockpit_cards", sa.Column(
        "subtitle", sa.Text(), nullable=False, server_default="",
        comment="副标题/功能描述（卡片名称下方一行）"))
    op.add_column("cockpit_cards", sa.Column(
        "soon", sa.Boolean(), nullable=False, server_default=sa.text("false"),
        comment="置灰占位（二期/规划中，不可点击）"))


def downgrade() -> None:
    op.drop_column("cockpit_cards", "soon")
    op.drop_column("cockpit_cards", "subtitle")
