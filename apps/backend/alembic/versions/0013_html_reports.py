"""add html_reports table

Revision ID: 0008_html_reports
Revises: 0007_chat_attachments
Create Date: 2026-09-14

- 新表 html_reports（AI 生成 HTML 报告：agent 工具同步生成落库，直出 text/html）
"""

from alembic import op

from app.persistence import models as pm

revision = "0013_html_reports"
down_revision = "0012_chat_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # 幂等建表（checkfirst=True）
    pm.HtmlReport.__table__.create(bind, checkfirst=True)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS html_reports")
