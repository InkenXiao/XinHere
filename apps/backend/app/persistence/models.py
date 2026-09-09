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
    """AI 对话会话主表。"""

    __tablename__ = "platform_sessions"

    session_id = Column(Uuid, primary_key=True, default=uuid.uuid4, comment="会话 ID（UUID）")
    user_id = Column(String(64), nullable=False, comment="所属用户 ID")
    title = Column(String(255), comment="会话标题")
    domain = Column(String(64), nullable=False, comment="业务域（如 finance）")
    plugin_set = Column(JSONB, nullable=False, comment="会话绑定的插件集合（JSON 数组）")
    plugin_set_hash = Column(String(64), nullable=False, comment="插件集合哈希（变更检测用）")
    status = Column(String(16), nullable=False, default="active", comment="状态：active=进行中 / archived=已归档")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="最后更新时间")

    __table_args__ = (Index("idx_psessions_user", "user_id", updated_at.desc()), {"comment": "AI 对话会话主表（Xin语对话的会话头，事件明细见 platform_session_events）"})


class PlatformSessionEvent(Base):
    """会话事件流（append-only）。"""

    __tablename__ = "platform_session_events"
    __table_args__ = {"comment": "会话事件流（用户/助手/工具事件明细，append-only）"}

    session_id = Column(Uuid, ForeignKey("platform_sessions.session_id"), primary_key=True, comment="所属会话 ID")
    seq = Column(BigInteger, primary_key=True, comment="会话内事件序号（0 起连续）")  # 会话内 0 起连续
    type = Column(String(64), nullable=False, comment="事件类型（如 user / assistant / tool）")
    time = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="事件时间")
    data = Column(JSONB, nullable=False, comment="事件内容（JSON，按类型异构）")
    ignorable = Column(Boolean, nullable=False, default=False, server_default="false", comment="回放时是否可忽略")
    turn = Column(Integer, comment="对话轮次")

    __table_args__ = (Index("idx_pevents_type", "session_id", "type"), {"comment": "会话事件流（用户/助手/工具事件明细，append-only）"})


class PlatformCompensation(Base):
    """插件副作用补偿任务。"""

    __tablename__ = "platform_compensations"
    __table_args__ = {"comment": "插件副作用补偿任务（执行失败/中断后的回滚动作队列）"}

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    session_id = Column(Uuid, nullable=False, comment="所属会话 ID")
    event_seq = Column(BigInteger, nullable=False, comment="触发补偿的事件序号")
    plugin_name = Column(String(64), nullable=False, comment="插件名")
    action = Column(String(128), nullable=False, comment="补偿动作")
    payload = Column(JSONB, nullable=False, comment="补偿参数（JSON）")
    status = Column(String(16), nullable=False, default="pending", comment="状态：pending=待执行 / done=完成 / failed=失败")
    retry_count = Column(Integer, nullable=False, default=0, comment="已重试次数")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")


class PlatformOperationLog(Base):
    """系统级操作日志：append-only，无 update/delete。"""

    __tablename__ = "platform_operation_logs"
    __table_args__ = {"comment": "系统级操作日志（append-only，所有读写操作留痕）"}

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="发生时间")
    user_id = Column(String(64), nullable=False, comment="操作用户 ID")
    session_id = Column(Uuid, comment="关联会话 ID")
    channel = Column(String(16), nullable=False, comment="渠道（如 page / api）")
    actor = Column(String(128), nullable=False, default="", server_default="", comment="操作主体（如 page:login）")
    plugin_name = Column(String(64), comment="插件名（插件操作时记录）")
    entity = Column(String(128), nullable=False, comment="操作实体（表名/对象名）")
    operation = Column(String(8), nullable=False, comment="操作类型：select / insert / update")
    record_key = Column(String(128), comment="操作记录的主键")
    detail = Column(JSONB, nullable=False, default=dict, server_default="{}", comment="操作明细（JSON）")
    client_ip = Column(String(45), comment="客户端 IP")
    entry_point = Column(String(128), comment="入口（如 POST /api/v1/auth/login）")
    request_id = Column(String(64), comment="请求 ID（链路追踪）")

    __table_args__ = (
        Index("idx_oplogs_entity", "entity", occurred_at.desc()),
        Index("idx_oplogs_user", "user_id", occurred_at.desc()),
        Index("idx_oplogs_sess", "session_id", occurred_at.desc()),
        {"comment": "系统级操作日志（append-only，所有读写操作留痕）"},
    )


class PlatformProjection(Base):
    """会话投影状态。"""

    __tablename__ = "platform_projections"
    __table_args__ = {"comment": "会话投影状态（事件流折叠后的当前视图，按投影键存取）"}

    session_id = Column(Uuid, nullable=False, primary_key=True, comment="所属会话 ID")
    key = Column(String(64), nullable=False, primary_key=True, comment="投影键（如 todo_state）")
    state_version = Column(Integer, nullable=False, default=1, comment="投影状态版本")
    seq = Column(BigInteger, nullable=False, default=0, comment="已投影至的事件序号")
    value = Column(JSONB, nullable=False, default=dict, comment="投影值（JSON）")


class CockpitEntry(Base):
    """Xin台入口配置（launch 目标；配置表非业务数据，删除为硬删）。"""

    __tablename__ = "cockpit_entries"
    __table_args__ = {"comment": "Xin台入口配置（可直达的业务系统入口，配置表）"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    key = Column(Text, nullable=False, unique=True, comment="入口标识（唯一）")
    name = Column(Text, nullable=False, comment="入口名称")
    entry_path = Column(Text, nullable=False, comment="XuanPu 站内路径（如 /pro/）")  # XuanPu 站内路径, 如 /pro/
    icon = Column(Text, comment="图标标识")
    sort = Column(Integer, nullable=False, default=0, server_default="0", comment="排序值（升序）")
    enabled = Column(Boolean, nullable=False, default=True, server_default="true", comment="是否启用")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")


class CockpitGroup(Base):
    """Xin台卡片分组配置（AI工作台 / AI技能 / 业务系统…；配置表，硬删）。"""

    __tablename__ = "cockpit_groups"
    __table_args__ = {"comment": "Xin台卡片分组配置（如 AI工作台 / AI技能 / 业务系统，配置表）"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    key = Column(Text, nullable=False, unique=True, comment="分组标识（唯一，如 workbench）")
    name = Column(Text, nullable=False, comment="分组名称（如 AI工作台）")
    sort = Column(Integer, nullable=False, default=0, server_default="0", comment="排序值（升序）")
    enabled = Column(Boolean, nullable=False, default=True, server_default="true", comment="是否启用")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")


class CockpitCard(Base):
    """Xin台卡片配置：link=页面跳转（link_url 可配）/ task=技能任务卡 / meeting=实时会议卡。"""

    __tablename__ = "cockpit_cards"
    __table_args__ = {"comment": "Xin台卡片配置（跳转/技能/会议三类卡片，配置表）"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    key = Column(Text, nullable=False, unique=True, comment="卡片标识（唯一，如 minutes）")
    group_key = Column(Text, nullable=False, comment="所属分组标识（cockpit_groups.key）")  # 所属分组 cockpit_groups.key
    name = Column(Text, nullable=False, comment="卡片名称（如 实时纪要）")
    kind = Column(String(16), nullable=False, default="task", comment="卡片类型：link=页面跳转 / task=技能任务 / meeting=实时会议")  # link / task / meeting
    link_url = Column(Text, comment="跳转地址（/ 开头为 XuanPu 站内免登路径，否则外链）")  # link 卡跳转地址：/ 开头为 XuanPu 站内路径（走免登），否则外链
    skill_id = Column(Integer, comment="绑定的 XuanPu 技能 ID（可空，按名称回退匹配）")  # task 卡绑定的 XuanPu 技能 id（可空，按名称回退匹配）
    skill_name = Column(Text, comment="绑定的技能名称（ID 未配置时回退匹配用）")  # task 卡绑定的技能名（回退匹配用）
    icon = Column(Text, comment="图标标识")
    sort = Column(Integer, nullable=False, default=0, server_default="0", comment="排序值（升序）")
    enabled = Column(Boolean, nullable=False, default=True, server_default="true", comment="是否启用")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")


# ---------------- 业务表（BusinessBase 三条红线） ----------------


class TaskRecord(BusinessBase):
    """历史任务（左侧任务抽屉）：Xin语对话记录（chat，引用 platform_sessions）与 Xin台 AI 能力
    执行记录（exec）统一树；kind=folder 为用户文件夹（scope 标明归属 chat / exec 列表）。逻辑删除。"""

    __tablename__ = "task_records"
    __table_args__ = (Index("idx_task_records_user", "user_id", "scope", "kind"),
                      {"comment": "历史任务（Xin语对话记录与 Xin台执行记录的统一树，含用户文件夹，逻辑删除）"})

    id = Column(String(36), primary_key=True, default=gen_id, comment="记录 ID（UUID）")
    user_id = Column(String(36), nullable=False, index=True, comment="所属用户 ID")
    kind = Column(String(16), nullable=False, comment="记录类型：chat=对话 / exec=执行记录 / folder=文件夹")  # chat / exec / folder
    scope = Column(String(16), nullable=False, default="chat", comment="所属列表：chat=Xin语对话 / exec=Xin台执行")  # 所属列表：chat / exec
    title = Column(String(255), nullable=False, default="", comment="名称（对话标题 / 执行标题 / 文件夹名）")
    status = Column(String(16), nullable=False, default="", comment="执行状态：running=运行中 / success=成功 / failed=失败 / stopped=已停止")  # exec: running/success/failed/stopped
    ref_id = Column(String(64), comment="引用 ID（chat=会话 ID，exec=来源卡片标识）")  # chat: platform_sessions.session_id
    detail = Column(JSONB, nullable=False, default=dict, comment="扩展信息（JSON，如执行输出摘要、会议时长）")  # exec 输出摘要 / 会议时长等
    parent_id = Column(String(36), comment="上级文件夹 ID（顶层为 NULL）")  # 上级文件夹 id（顶层为 NULL）
    sort = Column(Integer, nullable=False, default=0, server_default="0", comment="同层排序值（降序展示，大者在前）")  # 同层内降序展示（大者在前）


class HeroCard(BusinessBase):
    """Xin语页用户自定义卡片（链接 / 技能），按用户维度存储。逻辑删除。"""

    __tablename__ = "hero_cards"
    __table_args__ = (Index("idx_hero_cards_user", "user_id", "is_delete"),
                      {"comment": "Xin语页用户自定义卡片（链接/技能，按用户维度，逻辑删除）"})

    id = Column(String(36), primary_key=True, default=gen_id, comment="卡片 ID（UUID）")
    user_id = Column(String(36), nullable=False, index=True, comment="所属用户 ID")
    name = Column(String(128), nullable=False, comment="卡片名称")
    kind = Column(String(16), nullable=False, default="link", comment="卡片类型：link=链接 / skill=技能")  # link / skill
    link_url = Column(Text, comment="跳转地址（link 卡）")
    skill_id = Column(Integer, comment="绑定的技能 ID（skill 卡）")
    skill_name = Column(Text, comment="绑定的技能名称（skill 卡）")
    sort = Column(Integer, nullable=False, default=0, server_default="0", comment="排序值（升序）")


class SysUser(BusinessBase):
    __tablename__ = "sys_users"
    __table_args__ = {"comment": "系统用户表（本地账号 + 统一身份登录账号）"}

    user_id = Column(String(36), primary_key=True, default=gen_id, comment="用户 ID（UUID）")
    username = Column(String(64), nullable=False, unique=True, comment="登录账号（唯一）")
    password_hash = Column(String(128), nullable=False, comment="口令哈希（bcrypt；统一身份登录建号为空）")
    display_name = Column(String(64), nullable=False, comment="姓名")
    role = Column(String(32), nullable=False, comment="角色：hq_finance=总部财务（含管理员）/ investee_finance=被投企业财务")  # hq_finance（含 admin）/ investee_finance
    company = Column(String(64), comment="所属公司")
    # 账号来源: local=密码登录 / sso=统一身份登录 (JIT 建号, 无密码)
    auth_source = Column(String(16), nullable=False, default="local", server_default="local",
                         comment="账号来源：local=本地口令 / sso=统一身份登录（自动建号，无本地口令）")
    # XuanPu 用户绑定 (sso 建号时写入, 用于 launch 免登)
    xuanpu_user_id = Column(String(64), unique=True, comment="绑定的 XuanPu 用户 ID（站内系统免登跳转用）")


class SysAuthToken(BusinessBase):
    __tablename__ = "sys_auth_tokens"
    __table_args__ = {"comment": "登录令牌表（本地会话 token 与 XuanPu 网关令牌双轨）"}

    token = Column(String(128), primary_key=True, comment="访问令牌")
    user_id = Column(String(36), nullable=False, comment="所属用户 ID")
    expires_at = Column(DateTime(timezone=True), nullable=False, comment="过期时间")
    # XuanPu 网关 token (双轨鉴权: 有未过期值走 Bearer, 否则回落 X-User-Name)
    xuanpu_token = Column(Text, comment="XuanPu 网关令牌（未过期时走 Bearer 鉴权，否则回落 X-User-Name）")
    xuanpu_token_exp = Column(DateTime(timezone=True), comment="XuanPu 网关令牌过期时间")


class BizTask(BusinessBase):
    __tablename__ = "biz_tasks"
    __table_args__ = {"comment": "业务任务表（风险填报 / 资金保障 / KPI 填报等任务的总发起单）"}

    task_id = Column(String(36), primary_key=True, default=gen_id, comment="任务 ID（UUID）")
    scene = Column(String(32), nullable=False, comment="任务场景：risk_fill=风险填报 / cash_guarantee=资金保障 / kpi_fill=KPI 填报 / ms_feedback=里程碑反馈 / lamp_adjust=灯色调整 / report=报告 / generic=通用")  # risk_fill/cash_guarantee/kpi_fill/ms_feedback/lamp_adjust/report/generic
    title = Column(String(255), nullable=False, comment="任务标题")
    dispatcher_id = Column(String(36), nullable=False, comment="发起人用户 ID")
    payload = Column(JSONB, nullable=False, default=dict, comment="任务参数（JSON，按场景异构）")
    period = Column(String(32), comment="所属期间（如 2026-08）")
    status = Column(String(16), nullable=False, default="open", comment="状态：open=进行中 / closed=已关闭")  # open / closed


class BizTodo(BusinessBase):
    __tablename__ = "biz_todos"
    __table_args__ = (Index("idx_todos_assignee", "assignee_id", "status"),
                      {"comment": "待办表（任务分派到人的待办 / 审核 / 反馈事项）"})

    todo_id = Column(String(36), primary_key=True, default=gen_id, comment="待办 ID（UUID）")
    task_id = Column(String(36), nullable=False, comment="所属任务 ID（biz_tasks.task_id）")
    assignee_id = Column(String(36), nullable=False, comment="负责人用户 ID")
    kind = Column(String(32), nullable=False, default="action", comment="类型：action=待办 / na_confirm=不适用确认 / feedback_review=反馈复核 / review=审核")  # action/na_confirm/feedback_review/review
    scene = Column(String(32), nullable=False, default="generic", comment="场景（同 biz_tasks.scene）")
    title = Column(String(255), nullable=False, comment="待办标题")
    sub = Column(String(255), nullable=False, default="", comment="副标题/说明")
    status = Column(String(32), nullable=False, default="pending", comment="状态（如 pending=待处理）")
    lamp = Column(String(1), comment="灯色：r=红 / y=黄 / g=绿")
    ref = Column(JSONB, nullable=False, default=dict, comment="引用信息（JSON，如关联指标、公司）")
    feedback_text = Column(Text, comment="反馈内容")
    na_reason = Column(Text, comment="不适用原因")
    na_comment = Column(Text, comment="不适用备注")
    due = Column(DateTime(timezone=True), comment="截止时间")


class RiskFillBatch(BusinessBase):
    __tablename__ = "risk_fill_batches"
    __table_args__ = {"comment": "风险填报批次表（一次风险信息收集的发起批次）"}

    batch_id = Column(String(36), primary_key=True, default=gen_id, comment="批次 ID（UUID）")
    period = Column(String(32), nullable=False, comment="所属期间（如 2026-08）")
    dispatcher_id = Column(String(36), nullable=False, comment="发起人用户 ID")
    status = Column(String(16), nullable=False, default="collecting", comment="状态：collecting=收集中 / done=已完成")  # collecting / done


class RiskFillReport(BusinessBase):
    __tablename__ = "risk_fill_reports"
    __table_args__ = (Index("idx_risk_reports_batch", "batch_id", "company"),
                      {"comment": "风险填报报告表（批次内按公司一行的填报状态与灯色汇总）"})

    report_id = Column(String(36), primary_key=True, default=gen_id, comment="报告 ID（UUID）")
    batch_id = Column(String(36), nullable=False, comment="所属批次 ID（risk_fill_batches.batch_id）")
    company = Column(String(64), nullable=False, comment="公司名称")
    status = Column(String(16), nullable=False, default="unfilled", comment="状态：unfilled=未填 / filled=已填 / reviewed=已复核")  # unfilled/filled/reviewed
    lamp_r = Column(Integer, nullable=False, default=0, comment="红灯条目数")
    lamp_y = Column(Integer, nullable=False, default=0, comment="黄灯条目数")
    lamp_g = Column(Integer, nullable=False, default=0, comment="绿灯条目数")


class RiskFillItem(BusinessBase):
    __tablename__ = "risk_fill_items"
    __table_args__ = (Index("idx_risk_items_report", "report_id", "idx"),
                      {"comment": "风险填报条目表（报告内逐条风险项的灯色与字段值）"})

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    report_id = Column(String(36), nullable=False, comment="所属报告 ID（risk_fill_reports.report_id）")
    idx = Column(Integer, nullable=False, comment="条目序号（1-16）")  # 1-16
    name = Column(String(128), nullable=False, comment="条目名称")
    lamp = Column(String(1), nullable=False, default="g", comment="灯色：r=红 / y=黄 / g=绿")
    fields = Column(JSONB, nullable=False, default=list, comment="条目字段值（JSON 数组 [{k,v,pf}]）")  # [{k,v,pf?}]


class CashGuaranteeReport(BusinessBase):
    __tablename__ = "cash_guarantee_reports"
    __table_args__ = {"comment": "资金保障报告表（按公司+期间的资金保障能力填报）"}

    form_id = Column(String(36), primary_key=True, default=gen_id, comment="报告 ID（UUID）")
    company = Column(String(64), nullable=False, comment="公司名称")
    period = Column(String(32), nullable=False, comment="所属期间（如 2026-08）")
    avail_cash = Column(Float, nullable=False, default=0, comment="可用资金（万元）")  # 金额单位：万元
    pooled_fund = Column(Float, nullable=False, default=0, comment="归集资金（万元）")
    avail_credit = Column(Float, nullable=False, default=0, comment="可用授信（万元）")
    monthly_outflow = Column(Float, nullable=False, default=0, comment="月度资金流出（万元）")
    ratio = Column(Float, nullable=False, default=0, comment="资金保障倍数")
    lamp = Column(String(1), nullable=False, default="g", comment="灯色：r=红 / y=黄 / g=绿")
    status = Column(String(16), nullable=False, default="draft", comment="状态：draft=草稿 / submitted=已提交 / reviewed=已复核")  # draft/submitted/reviewed
    session_id = Column(Uuid, comment="关联会话 ID（AI 填报来源）")


class KpiBatch(BusinessBase):
    __tablename__ = "kpi_batches"
    __table_args__ = {"comment": "KPI 填报批次表（一次 KPI 信息收集的发起批次）"}

    batch_id = Column(String(36), primary_key=True, default=gen_id, comment="批次 ID（UUID）")
    period = Column(String(32), nullable=False, comment="所属期间（如 2026-08）")
    dispatcher_id = Column(String(36), nullable=False, comment="发起人用户 ID")
    status = Column(String(16), nullable=False, default="collecting", comment="状态：collecting=收集中 / done=已完成")


class KpiIndicator(BusinessBase):
    __tablename__ = "kpi_indicators"
    __table_args__ = (Index("idx_kpi_ind_batch", "batch_id", "company"),
                      {"comment": "KPI 指标表（批次内按公司+维度的指标填报）"})

    indicator_id = Column(String(36), primary_key=True, default=gen_id, comment="指标 ID（UUID）")
    batch_id = Column(String(36), nullable=False, comment="所属批次 ID（kpi_batches.batch_id）")
    company = Column(String(64), nullable=False, comment="公司名称")
    dim = Column(String(32), nullable=False, default="", comment="指标维度")
    name = Column(String(128), nullable=False, comment="指标名称")
    kpi_type = Column(String(32), nullable=False, default="定量", comment="指标类型（定量 / 定性）")
    content = Column(Text, nullable=False, default="", comment="指标内容/口径")
    base_score = Column(String(16), nullable=False, default="", comment="基准分")
    max_score = Column(String(16), nullable=False, default="", comment="封顶分")
    status = Column(String(16), nullable=False, default="unfilled", comment="状态：unfilled=未填 / filled=已填 / reviewed=已复核")  # unfilled/filled/reviewed


class KpiMilestone(BusinessBase):
    __tablename__ = "kpi_milestones"
    __table_args__ = (Index("idx_kpi_ms_indicator", "indicator_id"),
                      {"comment": "KPI 里程碑表（指标下的阶段目标与完成情况）"})

    milestone_id = Column(String(36), primary_key=True, default=gen_id, comment="里程碑 ID（UUID）")
    indicator_id = Column(String(36), nullable=False, comment="所属指标 ID（kpi_indicators.indicator_id）")
    content = Column(Text, nullable=False, default="", comment="里程碑内容")
    plan_date = Column(String(32), nullable=False, default="", comment="计划完成时间")
    material = Column(String(255), nullable=False, default="", comment="佐证材料")
    status = Column(String(16), nullable=False, default="pending", comment="状态（如 pending=未完成）")


class KpiMsFeedback(BusinessBase):
    __tablename__ = "kpi_ms_feedbacks"
    __table_args__ = {"comment": "里程碑进展反馈表（被投企业按里程碑提交的进展）"}

    feedback_id = Column(String(36), primary_key=True, default=gen_id, comment="反馈 ID（UUID）")
    milestone_id = Column(String(36), nullable=False, comment="所属里程碑 ID（kpi_milestones.milestone_id）")
    batch_id = Column(String(36), nullable=False, default="", comment="所属批次 ID（kpi_batches.batch_id）")
    company = Column(String(64), nullable=False, comment="公司名称")
    status = Column(String(32), nullable=False, default="进行中", comment="进展状态（如 进行中 / 已完成）")
    progress = Column(Integer, nullable=False, default=0, comment="进展百分比（0-100）")
    actual_date = Column(String(32), comment="实际完成时间")
    lamp = Column(String(1), nullable=False, default="g", comment="灯色：r=红 / y=黄 / g=绿")
    status_note = Column(Text, comment="状态说明")
    review_status = Column(String(16), nullable=False, default="draft", comment="复核状态：draft=草稿 / submitted=已提交 / reviewed=已复核")  # draft/submitted/reviewed


class KpiLampAdjustment(BusinessBase):
    __tablename__ = "kpi_lamp_adjustments"
    __table_args__ = {"comment": "KPI 灯色调整记录表（人工调灯留痕）"}

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    company = Column(String(64), nullable=False, comment="公司名称")
    indicator_name = Column(String(128), nullable=False, comment="指标名称")
    old_lamp = Column(String(1), nullable=False, comment="调整前灯色")
    new_lamp = Column(String(1), nullable=False, comment="调整后灯色")
    reason = Column(Text, nullable=False, default="", comment="调整原因")
    operator = Column(String(64), nullable=False, default="", comment="操作人")


class PitReport(BusinessBase):
    __tablename__ = "pit_reports"
    __table_args__ = {"comment": "投后报告表（AI 生成的投后管理报告）"}

    report_id = Column(String(36), primary_key=True, default=gen_id, comment="报告 ID（UUID）")
    company_ids = Column(JSONB, nullable=False, default=list, comment="覆盖公司 ID 列表（JSON 数组）")
    period = Column(String(32), nullable=False, comment="所属期间（如 2026-08）")
    outline = Column(JSONB, nullable=False, default=list, comment="报告大纲（JSON 数组）")
    content = Column(Text, nullable=False, default="", comment="报告正文")
    status = Column(String(16), nullable=False, default="outlining", comment="状态：outlining=生成大纲 / draft=撰写中 / done=已完成")  # outlining/draft/done


class KbSource(BusinessBase):
    __tablename__ = "kb_sources"
    __table_args__ = {"comment": "知识库来源表（可检索的内部/外部知识库）"}

    kb_id = Column(String(36), primary_key=True, comment="知识库 ID")
    name = Column(String(128), nullable=False, comment="知识库名称")
    parent_id = Column(String(36), comment="上级知识库 ID（顶层为 NULL）")
    kb_type = Column(String(16), nullable=False, default="internal", comment="类型：internal=内部 / external=外部")  # internal/external
    mcp_ref = Column(String(128), comment="MCP 引用标识")
