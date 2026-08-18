"""skill_templates + user_skills（技能模版 / 用户技能启用配置）

Revision ID: 0002_skill_tables
Revises: 0001_initial
Create Date: 2026-08-18
"""
from alembic import op

import app.persistence.models as m

revision = "0002_skill_tables"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_TABLES = ("skill_templates", "user_skills")


def upgrade() -> None:
    bind = op.get_bind()
    m.SkillTemplate.__table__.create(bind, checkfirst=True)
    m.UserSkill.__table__.create(bind, checkfirst=True)
    # 红线 2：updated_at 由 DB 触发器维护（set_updated_at 函数由 0001 创建）
    for table in _TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at()
            """
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
    op.execute("DROP TABLE IF EXISTS user_skills")
    op.execute("DROP TABLE IF EXISTS skill_templates")
