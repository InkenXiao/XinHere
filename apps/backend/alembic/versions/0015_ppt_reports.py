"""PPT 报告表 ppt_reports（AI 生成 PPT 财务风险报告）

Revision ID: 0011_ppt_reports
Revises: 0010_cockpit_config
Create Date: 2026-09-20
"""
from alembic import op

import app.persistence.models as m

revision = "0015_ppt_reports"
down_revision = "0014_cockpit_scms_columns"
branch_labels = None
depends_on = None

_TABLE = "ppt_reports"  # BusinessBase：updated_at 由 DB 触发器 set_updated_at() 维护


def upgrade() -> None:
    bind = op.get_bind()
    m.PptReport.__table__.create(bind, checkfirst=True)
    # 红线 2：updated_at 由 DB 触发器维护（幂等：先 DROP 再 CREATE）
    op.execute(f"DROP TRIGGER IF EXISTS trg_{_TABLE}_updated_at ON {_TABLE}")
    op.execute(
        f"""
        CREATE TRIGGER trg_{_TABLE}_updated_at
        BEFORE UPDATE ON {_TABLE}
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_{_TABLE}_updated_at ON {_TABLE}")
    op.execute("DROP TABLE IF EXISTS ppt_reports")
