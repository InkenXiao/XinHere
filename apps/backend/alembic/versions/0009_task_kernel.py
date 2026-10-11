"""任务内核成态：biz_tasks/biz_todos 增强 + 交付物/调灯流水/备注 + 身份组织主数据

Revision ID: 0004_task_kernel
Revises: 0003_add_comments
Create Date: 2026-09-04

- biz_tasks：+parent_task_id/template_key/related_system/review_required/due/
  end_policy/ended_at/ended_by/end_note/spawn_meta；status 词表 open|ended（旧值 closed 兼容读）
- biz_todos：+payload/completion_text/return_reason/redispatch_reason/lamp_agg/remind_count/
  last_reminded_at/submitted_at/returned_at/completed_at/na_at/session_id；
  存量 feedback_submitted → submitted（两段式审核词表收敛）
- 新表：task_artifacts / task_lamp_log / task_remarks /
  sys_org_units / sys_user_groups / sys_user_group_members
- sys_users：+scms_user_id（唯一）/ org_id
"""
from alembic import op

import app.persistence.models as m

revision = "0009_task_kernel"
down_revision = "0008_skill_tables_restore"
branch_labels = None
depends_on = None

_NEW_TABLES = (
    "task_artifacts",
    "task_lamp_log",
    "task_remarks",
    "sys_org_units",
    "sys_user_groups",
    "sys_user_group_members",
)

# (表, 列名, DDL 片段——与 models.py 定义保持一致)
_TASK_COLUMNS = (
    ("biz_tasks", "parent_task_id", "VARCHAR(36)"),
    ("biz_tasks", "template_key", "VARCHAR(64)"),
    ("biz_tasks", "related_system", "VARCHAR(128)"),
    ("biz_tasks", "review_required", "BOOLEAN NOT NULL DEFAULT false"),
    ("biz_tasks", "due", "TIMESTAMPTZ"),
    ("biz_tasks", "end_policy", "VARCHAR(16) NOT NULL DEFAULT 'all_done'"),
    ("biz_tasks", "ended_at", "TIMESTAMPTZ"),
    ("biz_tasks", "ended_by", "VARCHAR(36)"),
    ("biz_tasks", "end_note", "TEXT"),
    ("biz_tasks", "spawn_meta", "JSONB NOT NULL DEFAULT '{}'"),
    ("biz_todos", "payload", "JSONB NOT NULL DEFAULT '{}'"),
    ("biz_todos", "completion_text", "TEXT"),
    ("biz_todos", "return_reason", "TEXT"),
    ("biz_todos", "redispatch_reason", "TEXT"),
    ("biz_todos", "lamp_agg", "JSONB NOT NULL DEFAULT '{}'"),
    ("biz_todos", "remind_count", "INTEGER NOT NULL DEFAULT 0"),
    ("biz_todos", "last_reminded_at", "TIMESTAMPTZ"),
    ("biz_todos", "submitted_at", "TIMESTAMPTZ"),
    ("biz_todos", "returned_at", "TIMESTAMPTZ"),
    ("biz_todos", "completed_at", "TIMESTAMPTZ"),
    ("biz_todos", "na_at", "TIMESTAMPTZ"),
    ("biz_todos", "session_id", "UUID"),
    ("sys_users", "scms_user_id", "VARCHAR(64)"),
    ("sys_users", "org_id", "VARCHAR(36)"),
)

_NEW_INDEXES = (
    ("uq_sys_users_scms", "sys_users", "scms_user_id", True),
    ("idx_tasks_parent", "biz_tasks", "parent_task_id", False),
    ("idx_tasks_tpl_period", "biz_tasks", "template_key, period", False),
    ("idx_todos_task_status", "biz_todos", "task_id, status", False),
    ("idx_todos_payload", "biz_todos", None, False),  # GIN，单独建
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1) 新表（幂等：应用启动自检可能已建过）
    for cls in (m.TaskArtifact, m.TaskLampLog, m.TaskRemark,
                m.SysOrgUnit, m.SysUserGroup, m.SysUserGroupMember):
        cls.__table__.create(bind, checkfirst=True)
    for table in _NEW_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at "
            f"BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )

    # 2) 存量表加列（幂等）
    insp_cols = {}
    from sqlalchemy import inspect
    insp = inspect(bind)
    for table in ("biz_tasks", "biz_todos", "sys_users"):
        insp_cols[table] = {c["name"] for c in insp.get_columns(table)}
    for table, col, ddl in _TASK_COLUMNS:
        if col not in insp_cols[table]:
            op.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

    # 3) 索引（幂等）
    for name, table, cols, unique in _NEW_INDEXES:
        if cols is None:
            op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} USING GIN (payload)")
        else:
            op.execute(
                f"CREATE {'UNIQUE ' if unique else ''}INDEX IF NOT EXISTS {name} ON {table} ({cols})"
            )

    # 4) 存量状态收敛：feedback_submitted → submitted（两段式词表）
    op.execute("UPDATE biz_todos SET status='submitted' WHERE status='feedback_submitted'")
    # 旧任务状态词 closed 保留（dashboard 兼容读 closed|ended），不强制改写。

    # 5) 守护租约锁表（§4.9 多副本单实例互斥；平台配置表）
    m.PlatformDaemonLock.__table__.create(bind, checkfirst=True)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform_daemon_locks")
    for table in _NEW_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
    for table, col, _ddl in _TASK_COLUMNS:
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}")
    for name, *_ in _NEW_INDEXES:
        pass  # 列删除时索引随列/表自动清理；独立索引随上面 DROP COLUMN 失效
