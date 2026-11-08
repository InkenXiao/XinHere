"""P9 会话执行并发化：新增 platform_session_runs（会话 run 互斥与协调表）

一会话一行原位变更：claim = 条件 UPSERT（无行 / 终态 / 租约过期 → 置 running），
0 行受影响 = 会话忙。摘除全局 _cp_lock 的互斥锚点（同会话跨副本执行互斥、
租约接管、epoch fencing、resume 重建上下文）。纯新增表，回滚零迁移倒退。

Revision ID: 0023_session_runs
Revises: 0022_compensation_status_widen
Create Date: 2026-11-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0023_session_runs"
down_revision = "0022_compensation_status_widen"
branch_labels = None
depends_on = None

_TABLE = "platform_session_runs"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("session_id", UUID(as_uuid=True), primary_key=True,
                  comment="会话 ID（PK 原位变更；互斥锚点）"),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False,
                  comment="本次 run 标识（每次 claim 重新生成，fencing 用）"),
        sa.Column("status", sa.String(24), nullable=False,
                  comment="状态：running=执行中 / waiting_interrupt=等待卡片 / done=完成 / error=异常"),
        sa.Column("owner", sa.String(128), nullable=False, server_default="",
                  comment="持有者标识（host:pid）"),
        sa.Column("epoch", sa.BigInteger, nullable=False, server_default="1",
                  comment="抢占代次（fencing token），每次 claim/接管 +1"),
        sa.Column("turn", sa.Integer, comment="当前轮次（start 路由 claim 时确定）"),
        sa.Column("interrupt_id", sa.String(64), comment="waiting_interrupt 状态的中断 id（重建恢复用）"),
        sa.Column("component_id", sa.String(64), comment="waiting_interrupt 状态的组件 id（重建恢复用）"),
        sa.Column("user_id", sa.String(64), nullable=False, server_default="",
                  comment="归属用户（resume 重建图装配上下文）"),
        sa.Column("request_id", sa.String(64), nullable=False, server_default="",
                  comment="触发本次 run 的请求 id（追踪）"),
        sa.Column("model_key", sa.String(64), nullable=False, server_default="",
                  comment="模型通道（run 期重建图保持一致）"),
        sa.Column("skill_key", sa.String(64), nullable=False, server_default="",
                  comment="本地技能 key（重建上下文）"),
        sa.Column("skill_source", sa.String(32), nullable=False, server_default="",
                  comment="技能来源：local / xuanpu（重建上下文）"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False,
                  comment="租约到期时间（持锁者周期续期，过期即可被接管）"),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
                  comment="最近心跳时间"),
        sa.Column("detail", JSONB, nullable=False, server_default="{}",
                  comment="结构化补充信息（对账原因、接管记录等）"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
                  comment="首行创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
                  comment="最近变更时间"),
        comment="会话 run 互斥与协调：同会话跨副本执行互斥 + 租约接管 + 重建上下文",
    )
    # 接管扫描与对账查询按状态过滤
    op.create_index(
        "ix_session_runs_status", _TABLE, ["status"],
        postgresql_where=sa.text("status in ('running', 'waiting_interrupt')"),
    )


def downgrade() -> None:
    # 纯新增表：回滚直接整表删除（P9 回滚不删表，此处仅供彻底退役用）
    op.drop_index("ix_session_runs_status", table_name=_TABLE)
    op.drop_table(_TABLE)
