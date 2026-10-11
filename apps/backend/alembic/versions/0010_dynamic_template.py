"""add dynamic_template and dynamic_fill_data tables

Revision ID: 0005_dynamic_template
Revises: 0004_task_kernel
Create Date: 2026-09-07

- 新表 dynamic_template（Excel 解析 + HTML 双缓存）/ dynamic_fill_data（填报数据 JSONB）
- biz_tasks 加列 dynamic_template_id（动态模板任务关联，varchar 避免跨库 FK）
"""
from alembic import op

from app.modules.dynamic_template import models as m

revision = "0010_dynamic_template"
down_revision = "0009_task_kernel"
branch_labels = None
depends_on = None

_TABLES = ("dynamic_template", "dynamic_fill_data")


def upgrade() -> None:
    bind = op.get_bind()

    # 1) 两张新表（幂等：应用启动自检可能已建过）
    for cls in (m.DynamicTemplate, m.DynamicFillData):
        cls.__table__.create(bind, checkfirst=True)
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at "
            f"BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )

    # 2) biz_tasks 加列（幂等）
    from sqlalchemy import inspect

    cols = {c["name"] for c in inspect(bind).get_columns("biz_tasks")}
    if "dynamic_template_id" not in cols:
        op.execute("ALTER TABLE biz_tasks ADD COLUMN dynamic_template_id VARCHAR(64)")
        op.execute(
            "COMMENT ON COLUMN biz_tasks.dynamic_template_id IS "
            "'动态填报模板 ID（关联 dynamic_template.template_id，类型 varchar 避免跨库 FK）；"
            "非空时待办走动态 HTML 填报'"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("ALTER TABLE biz_tasks DROP COLUMN IF EXISTS dynamic_template_id")
