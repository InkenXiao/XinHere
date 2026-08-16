"""initial: 平台表5 + 业务表14 + updated_at 触发器

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-16
"""
from alembic import op

from app.persistence.base import Base, BusinessBase
import app.persistence.models  # noqa: F401  确保全部表注册

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _business_tables() -> list[str]:
    import app.persistence.models as m

    tables = []
    for name in dir(m):
        obj = getattr(m, name)
        if isinstance(obj, type) and issubclass(obj, BusinessBase) and obj is not BusinessBase:
            tables.append(obj.__tablename__)
    return sorted(set(tables))


def attach_updated_at_trigger(table: str) -> None:
    op.execute(
        f"""
        CREATE TRIGGER trg_{table}_updated_at
        BEFORE UPDATE ON {table}
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in _business_tables():
        attach_updated_at_trigger(table)
    # 会话头 updated_at 也交给触发器（列表按 updated_at 排序）
    attach_updated_at_trigger("platform_sessions")


def downgrade() -> None:
    bind = op.get_bind()
    for table in _business_tables() + ["platform_sessions"]:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    Base.metadata.drop_all(bind=bind)
