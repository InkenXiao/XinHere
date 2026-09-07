"""技能模板表（历史桩）

原 0002_skill_tables 建 skill_templates / user_skills 两表；b2ac122 移除技能体系后
表与模型均已废弃。此桩仅修复 alembic 链完整性（运行库的 alembic_version 已指向
本 revision），不再建表：历史库遗留的两张表无代码引用，保留不处理；全新库不再建出。

Revision ID: 0002_skill_tables
Revises: 0001_initial
Create Date: 2026-08-18
"""

revision = "0002_skill_tables"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
