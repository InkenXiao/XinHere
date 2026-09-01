from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import Uuid

from .base import Base, BusinessBase


def gen_id() -> str:
    return str(uuid.uuid4())


# ---------------- 平台表（append-only，无 DELETE） ----------------


class PlatformSession(Base):
    __tablename__ = "platform_sessions"

    session_id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(String(64), nullable=False)
    title = Column(String(255))
    domain = Column(String(64), nullable=False)
    plugin_set = Column(JSONB, nullable=False)
    plugin_set_hash = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="active")  # active / archived
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (Index("idx_psessions_user", "user_id", updated_at.desc()),)


class PlatformSessionEvent(Base):
    __tablename__ = "platform_session_events"

    session_id = Column(Uuid, ForeignKey("platform_sessions.session_id"), primary_key=True)
    seq = Column(BigInteger, primary_key=True)  # 会话内 0 起连续
    type = Column(String(64), nullable=False)
    time = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    data = Column(JSONB, nullable=False)
    ignorable = Column(Boolean, nullable=False, default=False, server_default="false")
    turn = Column(Integer)

    __table_args__ = (Index("idx_pevents_type", "session_id", "type"),)


class PlatformCompensation(Base):
    __tablename__ = "platform_compensations"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(Uuid, nullable=False)
    event_seq = Column(BigInteger, nullable=False)
    plugin_name = Column(String(64), nullable=False)
    action = Column(String(128), nullable=False)
    payload = Column(JSONB, nullable=False)
    status = Column(String(16), nullable=False, default="pending")  # pending/done/failed
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PlatformOperationLog(Base):
    """系统级操作日志：append-only，无 update/delete。"""

    __tablename__ = "platform_operation_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    user_id = Column(String(64), nullable=False)
    session_id = Column(Uuid)
    channel = Column(String(16), nullable=False)
    actor = Column(String(128), nullable=False, default="", server_default="")
    plugin_name = Column(String(64))
    entity = Column(String(128), nullable=False)
    operation = Column(String(8), nullable=False)  # select/insert/update
    record_key = Column(String(128))
    detail = Column(JSONB, nullable=False, default=dict, server_default="{}")
    client_ip = Column(String(45))
    entry_point = Column(String(128))
    request_id = Column(String(64))

    __table_args__ = (
        Index("idx_oplogs_entity", "entity", occurred_at.desc()),
        Index("idx_oplogs_user", "user_id", occurred_at.desc()),
        Index("idx_oplogs_sess", "session_id", occurred_at.desc()),
    )


class PlatformProjection(Base):
    __tablename__ = "platform_projections"

    session_id = Column(Uuid, nullable=False, primary_key=True)
    key = Column(String(64), nullable=False, primary_key=True)
    state_version = Column(Integer, nullable=False, default=1)
    seq = Column(BigInteger, nullable=False, default=0)
    value = Column(JSONB, nullable=False, default=dict)


# ---------------- 业务表（BusinessBase 三条红线） ----------------


class SysUser(BusinessBase):
    __tablename__ = "sys_users"

    user_id = Column(String(36), primary_key=True, default=gen_id)
    username = Column(String(64), nullable=False, unique=True)
    password_hash = Column(String(128), nullable=False)
    display_name = Column(String(64), nullable=False)
    role = Column(String(32), nullable=False)  # hq_finance（含 admin）/ investee_finance
    company = Column(String(64))


class SysAuthToken(BusinessBase):
    __tablename__ = "sys_auth_tokens"

    token = Column(String(128), primary_key=True)
    user_id = Column(String(36), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class BizTask(BusinessBase):
    __tablename__ = "biz_tasks"

    task_id = Column(String(36), primary_key=True, default=gen_id)
    scene = Column(String(32), nullable=False)  # risk_fill/cash_guarantee/kpi_fill/ms_feedback/lamp_adjust/report/generic
    title = Column(String(255), nullable=False)
    dispatcher_id = Column(String(36), nullable=False)
    payload = Column(JSONB, nullable=False, default=dict)
    period = Column(String(32))
    status = Column(String(16), nullable=False, default="open")  # open / closed


class BizTodo(BusinessBase):
    __tablename__ = "biz_todos"

    todo_id = Column(String(36), primary_key=True, default=gen_id)
    task_id = Column(String(36), nullable=False)
    assignee_id = Column(String(36), nullable=False)
    kind = Column(String(32), nullable=False, default="action")  # action/na_confirm/feedback_review/review
    scene = Column(String(32), nullable=False, default="generic")
    title = Column(String(255), nullable=False)
    sub = Column(String(255), nullable=False, default="")
    status = Column(String(32), nullable=False, default="pending")
    lamp = Column(String(1))
    ref = Column(JSONB, nullable=False, default=dict)
    feedback_text = Column(Text)
    na_reason = Column(Text)
    na_comment = Column(Text)
    due = Column(DateTime(timezone=True))

    __table_args__ = (Index("idx_todos_assignee", "assignee_id", "status"),)


class RiskFillBatch(BusinessBase):
    __tablename__ = "risk_fill_batches"

    batch_id = Column(String(36), primary_key=True, default=gen_id)
    period = Column(String(32), nullable=False)
    dispatcher_id = Column(String(36), nullable=False)
    status = Column(String(16), nullable=False, default="collecting")  # collecting / done


class RiskFillReport(BusinessBase):
    __tablename__ = "risk_fill_reports"

    report_id = Column(String(36), primary_key=True, default=gen_id)
    batch_id = Column(String(36), nullable=False)
    company = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="unfilled")  # unfilled/filled/reviewed
    lamp_r = Column(Integer, nullable=False, default=0)
    lamp_y = Column(Integer, nullable=False, default=0)
    lamp_g = Column(Integer, nullable=False, default=0)

    __table_args__ = (Index("idx_risk_reports_batch", "batch_id", "company"),)


class RiskFillItem(BusinessBase):
    __tablename__ = "risk_fill_items"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(String(36), nullable=False)
    idx = Column(Integer, nullable=False)  # 1-16
    name = Column(String(128), nullable=False)
    lamp = Column(String(1), nullable=False, default="g")
    fields = Column(JSONB, nullable=False, default=list)  # [{k,v,pf?}]

    __table_args__ = (Index("idx_risk_items_report", "report_id", "idx"),)


class CashGuaranteeReport(BusinessBase):
    __tablename__ = "cash_guarantee_reports"

    form_id = Column(String(36), primary_key=True, default=gen_id)
    company = Column(String(64), nullable=False)
    period = Column(String(32), nullable=False)
    avail_cash = Column(Float, nullable=False, default=0)  # 金额单位：万元
    pooled_fund = Column(Float, nullable=False, default=0)
    avail_credit = Column(Float, nullable=False, default=0)
    monthly_outflow = Column(Float, nullable=False, default=0)
    ratio = Column(Float, nullable=False, default=0)
    lamp = Column(String(1), nullable=False, default="g")
    status = Column(String(16), nullable=False, default="draft")  # draft/submitted/reviewed
    session_id = Column(Uuid)


class KpiBatch(BusinessBase):
    __tablename__ = "kpi_batches"

    batch_id = Column(String(36), primary_key=True, default=gen_id)
    period = Column(String(32), nullable=False)
    dispatcher_id = Column(String(36), nullable=False)
    status = Column(String(16), nullable=False, default="collecting")


class KpiIndicator(BusinessBase):
    __tablename__ = "kpi_indicators"

    indicator_id = Column(String(36), primary_key=True, default=gen_id)
    batch_id = Column(String(36), nullable=False)
    company = Column(String(64), nullable=False)
    dim = Column(String(32), nullable=False, default="")
    name = Column(String(128), nullable=False)
    kpi_type = Column(String(32), nullable=False, default="定量")
    content = Column(Text, nullable=False, default="")
    base_score = Column(String(16), nullable=False, default="")
    max_score = Column(String(16), nullable=False, default="")
    status = Column(String(16), nullable=False, default="unfilled")  # unfilled/filled/reviewed

    __table_args__ = (Index("idx_kpi_ind_batch", "batch_id", "company"),)


class KpiMilestone(BusinessBase):
    __tablename__ = "kpi_milestones"

    milestone_id = Column(String(36), primary_key=True, default=gen_id)
    indicator_id = Column(String(36), nullable=False)
    content = Column(Text, nullable=False, default="")
    plan_date = Column(String(32), nullable=False, default="")
    material = Column(String(255), nullable=False, default="")
    status = Column(String(16), nullable=False, default="pending")

    __table_args__ = (Index("idx_kpi_ms_indicator", "indicator_id"),)


class KpiMsFeedback(BusinessBase):
    __tablename__ = "kpi_ms_feedbacks"

    feedback_id = Column(String(36), primary_key=True, default=gen_id)
    milestone_id = Column(String(36), nullable=False)
    batch_id = Column(String(36), nullable=False, default="")
    company = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="进行中")
    progress = Column(Integer, nullable=False, default=0)
    actual_date = Column(String(32))
    lamp = Column(String(1), nullable=False, default="g")
    status_note = Column(Text)
    review_status = Column(String(16), nullable=False, default="draft")  # draft/submitted/reviewed


class KpiLampAdjustment(BusinessBase):
    __tablename__ = "kpi_lamp_adjustments"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    company = Column(String(64), nullable=False)
    indicator_name = Column(String(128), nullable=False)
    old_lamp = Column(String(1), nullable=False)
    new_lamp = Column(String(1), nullable=False)
    reason = Column(Text, nullable=False, default="")
    operator = Column(String(64), nullable=False, default="")


class PitReport(BusinessBase):
    __tablename__ = "pit_reports"

    report_id = Column(String(36), primary_key=True, default=gen_id)
    company_ids = Column(JSONB, nullable=False, default=list)
    period = Column(String(32), nullable=False)
    outline = Column(JSONB, nullable=False, default=list)
    content = Column(Text, nullable=False, default="")
    status = Column(String(16), nullable=False, default="outlining")  # outlining/draft/done


class KbSource(BusinessBase):
    __tablename__ = "kb_sources"

    kb_id = Column(String(36), primary_key=True)
    name = Column(String(128), nullable=False)
    parent_id = Column(String(36))
    kb_type = Column(String(16), nullable=False, default="internal")  # internal/external
    mcp_ref = Column(String(128))
