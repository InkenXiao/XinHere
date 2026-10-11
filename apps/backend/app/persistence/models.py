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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import Uuid

from .base import Base, BusinessBase

# —— 模块自注册：SQLAlchemy metadata 需要 import 模型类才能在 alembic autogenerate 时被扫到 ——
from app.modules.dynamic_template import models as _dyn_tpl_models  # noqa: F401,E402  pylint: disable=unused-import,wrong-import-position,wrong-import-order
from app.modules.meeting import models as _meeting_models  # noqa: F401,E402


def gen_id() -> str:
    return str(uuid.uuid4())


# ---------------- 平台表（append-only，无 DELETE） ----------------


class PlatformSession(Base):
    """AI 对话会话头表：一行一个会话（聚合根）。"""

    __tablename__ = "platform_sessions"

    session_id = Column(Uuid, primary_key=True, default=uuid.uuid4, comment="会话 ID（UUID）")
    user_id = Column(String(64), nullable=False, comment="归属用户 ID（SCMS 用户，非归属访问一律 404）")
    title = Column(String(255), comment="会话标题")
    domain = Column(String(64), nullable=False, comment="业务域")
    plugin_set = Column(
        JSONB, nullable=False,
        comment="会话创建时锁定的插件清单快照（plugins/*/plugin.json）",
    )
    plugin_set_hash = Column(String(64), nullable=False, comment="插件清单 sha256 前 32 位（快照指纹）")
    status = Column(
        String(16), nullable=False, default="active",
        comment="状态：active=活跃 / archived=已归档（软删）",
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="更新时间（触发器维护；会话列表按其倒序，事件写入时锁本行串行化 seq 分配）",
    )

    __table_args__ = (
        Index("idx_psessions_user", "user_id", updated_at.desc()),
        {"comment": "AI 对话会话头表：一行一个会话（聚合根）；创建时锁定插件集快照，"
                    "status=archived 即归档软删，事件流全量保留可恢复"},
    )


class PlatformSessionEvent(Base):
    """会话事件日志（事件溯源本体）。"""

    __tablename__ = "platform_session_events"

    session_id = Column(Uuid, ForeignKey("platform_sessions.session_id"), primary_key=True, comment="所属会话 ID")
    seq = Column(BigInteger, primary_key=True, comment="会话内序号，0 起连续（锁会话头行取 max+1 分配）")
    type = Column(
        String(64), nullable=False,
        comment="契约事件类型：turn/*、user/message、assistant/*、tool/*、component/*、report/*、chart/* 及业务事件族",
    )
    time = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="事件时间")
    data = Column(JSONB, nullable=False, comment="事件负载（JSONB，结构随 type 变化）")
    ignorable = Column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="回放/压缩时可忽略（当前仅 todo/changed=true）",
    )
    turn = Column(Integer, comment="所属对话轮次")

    __table_args__ = (
        Index("idx_pevents_type", "session_id", "type"),
        {"comment": "会话事件日志（事件溯源本体）：append-only，(session_id, seq) 主键、会话内 seq 从 0 连续；"
                    "type 基于 packages/contracts 契约 fail-closed 校验"},
    )


class ChatAttachment(Base):
    """对话附件：问答输入区的文件上传元数据（本体在 MinIO/本地对象存储）。

    上传与会话解耦（开屏首问先传后建会话），发消息时按 id 列表绑定进 user/message 事件。
    """

    __tablename__ = "chat_attachments"

    attachment_id = Column(String(36), primary_key=True, default=gen_id, comment="附件 ID")
    user_id = Column(String(64), nullable=False, comment="上传人 ID（读取越权一律 404）")
    file_name = Column(String(255), nullable=False, comment="原始文件名")
    mime = Column(String(128), nullable=False, comment="MIME 类型")
    size = Column(BigInteger, nullable=False, comment="文件大小（字节）")
    storage_key = Column(String(255), nullable=False, comment="对象存储键（MinIO/本地磁盘统一键）")
    parse_status = Column(
        String(16), nullable=False, default="ok",
        comment="解析状态：ok=已提取文本 / image=图片走多模态视觉 / failed=无法提取（parse_error 说明）",
    )
    parse_error = Column(String(255), comment="解析失败原因（parse_status=failed 时）")
    parsed_text = Column(Text, comment="提取文本（超长截断，仅注入 LLM 上下文，不回传前端）")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="上传时间")

    __table_args__ = (
        Index("idx_chat_atts_user", "user_id", "created_at"),
        {"comment": "对话附件：问答链路文件上传元数据；文本解析结果随行存储，图片经视觉模型理解"},
    )


class PlatformCompensation(Base):
    """组件提交失败补偿留痕。"""

    __tablename__ = "platform_compensations"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    session_id = Column(Uuid, nullable=False, comment="所属会话 ID")
    event_seq = Column(BigInteger, nullable=False, comment="预分配的 submit 事件 seq")
    plugin_name = Column(String(64), nullable=False, comment="组件 kind（插件名）")
    action = Column(String(128), nullable=False, comment="补偿动作（固定 replay_submit）")
    payload = Column(JSONB, nullable=False, comment="补偿负载 {component_id, props, values}")
    status = Column(
        String(16), nullable=False, default="pending",
        comment="状态：pending=待重放 / done=已重放 / failed=失败",
    )
    retry_count = Column(Integer, nullable=False, default=0, comment="重放次数（预留）")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")

    __table_args__ = {
        "comment": "组件提交补偿留痕：component/submit 业务落库失败时记录 pending，"
                   "预留幂等 worker 重放（当前 v1 仅记录，无消费方）"
    }


class PlatformOperationLog(Base):
    """系统级操作日志：append-only，无 update/delete。"""

    __tablename__ = "platform_operation_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="发生时间")
    user_id = Column(String(64), nullable=False, comment="操作用户 ID（审计上下文）")
    session_id = Column(Uuid, comment="关联会话 ID（可空）")
    channel = Column(
        String(16), nullable=False,
        comment="操作渠道：page/agent/model/skill/mcp/tool/api/system",
    )
    actor = Column(
        String(128), nullable=False, default="", server_default="",
        comment="操作者：页面=用户 ID；AI 组件确认=「模型名+工具名」（AI 溯源红线）",
    )
    plugin_name = Column(String(64), comment="涉及插件/组件名")
    entity = Column(String(128), nullable=False, comment="被操作表名")
    operation = Column(
        String(8), nullable=False,
        comment="操作类型：select/insert/update",
    )
    record_key = Column(String(128), comment="被操作记录主键值")
    detail = Column(
        JSONB, nullable=False, default=dict, server_default="{}",
        comment="操作明细：select=条件摘要+命中行数 / insert=全量新值 / update=字段级 old→new diff",
    )
    client_ip = Column(String(45), comment="客户端 IP")
    entry_point = Column(String(128), comment="入口：HTTP「方法 路径」或 component:kind")
    request_id = Column(String(64), comment="请求链路 ID")

    __table_args__ = (
        Index("idx_oplogs_entity", "entity", occurred_at.desc()),
        Index("idx_oplogs_user", "user_id", occurred_at.desc()),
        Index("idx_oplogs_sess", "session_id", occurred_at.desc()),
        {"comment": "系统级操作审计日志：SQLAlchemy 全局钩子自动记录业务表 select/insert/update，"
                    "append-only 无 update/delete；detail 经脱敏瀑布（password/token/secret 值替换为 ***）"},
    )


class PlatformProjection(Base):
    """会话读模型（投影）。"""

    __tablename__ = "platform_projections"

    session_id = Column(Uuid, nullable=False, primary_key=True, comment="所属会话 ID")
    key = Column(String(64), nullable=False, primary_key=True, comment="投影键（当前仅 stats）")
    state_version = Column(Integer, nullable=False, default=1, comment="投影结构版本号")
    seq = Column(BigInteger, nullable=False, default=0, comment="已聚合到的事件 seq（增量游标）")
    value = Column(
        JSONB, nullable=False, default=dict,
        comment="投影值：stats={turns: 完成轮数, assistant_messages: 助手消息数}",
    )
    __table_args__ = {
        "comment": "会话读模型（投影）：按 key 聚合事件流结果，executor 每轮 run 成功后增量写入"
    }


class PlatformDaemonLock(Base):
    """后台守护租约锁（多副本部署下单实例互斥）。

    守护启动以「锁键 + 过期时间」抢占租约（DB 乐观锁）；持锁者周期续期，
    租约过期其他副本可接管。心跳与接管日志落 platform_operation_logs。
    """

    __tablename__ = "platform_daemon_locks"
    __table_args__ = {"comment": "后台守护租约锁：同一守护全局仅一份活跃（看门狗/WS 守护/晨间扫描等）"}

    lock_key = Column(String(64), primary_key=True, comment="锁键（守护名，全局唯一）")
    owner = Column(String(128), nullable=False, default="", server_default="",
                   comment="持锁者标识（host:pid）")
    heartbeat_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                          comment="最近心跳时间")
    expires_at = Column(DateTime(timezone=True), nullable=False,
                        comment="租约过期时间（持锁者周期续期；过期后其他副本可接管）")
    acquired_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                         comment="本次获锁时间")


# ---------------- 导航/卡片配置表（配置数据：硬删、无软删无审计，非业务数据） ----------------


class CockpitEntry(Base):
    """直达入口配置（站内免登路径，如 XuanPu 子系统；预留：Xin台壳接入时启用端点）。"""

    __tablename__ = "cockpit_entries"
    __table_args__ = {"comment": "直达入口配置（可直达的业务系统入口，配置表）"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    key = Column(Text, nullable=False, unique=True, comment="入口标识（唯一）")
    name = Column(Text, nullable=False, comment="入口名称")
    entry_path = Column(Text, nullable=False, comment="站内免登路径（如 /pro/）")
    icon = Column(Text, comment="图标标识")
    sort = Column(Integer, nullable=False, default=0, server_default="0", comment="排序值（升序）")
    enabled = Column(Boolean, nullable=False, default=True, server_default="true", comment="是否启用")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")


class CockpitGroup(Base):
    """卡片分组配置（surface 区分壳屏：dashboard=驾驶舱首页 / xin_tai=Xin台）。"""

    __tablename__ = "cockpit_groups"
    __table_args__ = (
        Index("ix_cockpit_groups_surface", "surface", "enabled"),
        {"comment": "卡片分组配置（按壳屏 surface 隔离，配置表）"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    key = Column(Text, nullable=False, unique=True, comment="分组标识（唯一，如 dashboard_system）")
    surface = Column(
        String(16), nullable=False, default="dashboard", server_default="dashboard",
        comment="归属壳屏：dashboard=驾驶舱首页 / xin_tai=Xin台",
    )
    name = Column(Text, nullable=False, comment="分组名称（如 系统）")
    sort = Column(Integer, nullable=False, default=0, server_default="0", comment="排序值（升序）")
    enabled = Column(Boolean, nullable=False, default=True, server_default="true", comment="是否启用")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")


class CockpitCard(Base):
    """卡片配置：action 决定点击行为；perm_key 交外部权限系统判权（空=不判权全员可见）。

    配置与授权解耦：本表只存"有什么卡/什么行为/权限资源标识"，"谁能看"由
    services/permissions.visible_perm_keys 运行时判定（未接入放行、判权失败 fail-closed）。
    """

    __tablename__ = "cockpit_cards"
    __table_args__ = (
        Index("ix_cockpit_cards_group", "group_key", "enabled"),
        {"comment": "卡片配置（出票/跳转/技能/弹窗/功能多行为，配置表）"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    key = Column(Text, nullable=False, unique=True, comment="卡片标识（唯一）")
    group_key = Column(Text, nullable=False, comment="所属分组标识（cockpit_groups.key）")
    name = Column(Text, nullable=False, comment="卡片名称")
    description = Column(Text, comment="卡片说明文字（如「生成 PPT 演示」「实时转写 · 自动生成纪要」，通用展示属性）")
    pending = Column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="占位态：true=渲染为「敬请期待」禁用卡（通用展示属性）",
    )
    action = Column(
        String(16), nullable=False, default="sso_scms", server_default="sso_scms",
        comment="点击行为类型（与前端渲染分支一一对应）：sso_scms / sso_kb / xuanpu_link / "
                "xuanpu_skill / skill_prompt / modal / feature",
    )
    params = Column(
        JSONB, nullable=False, default=dict, server_default="{}",
        comment="行为参数（按 action 约定键，后端透传不消费）：sso_scms={redirect} / xuanpu_link={url} / "
                "xuanpu_skill={skill_id,skill_name} / skill_prompt={skill} / modal={modal} / feature={feature}",
    )
    icon = Column(Text, comment="图标标识")
    sort = Column(Integer, nullable=False, default=0, server_default="0", comment="排序值（升序）")
    enabled = Column(Boolean, nullable=False, default=True, server_default="true", comment="是否启用（总开关）")
    perm_key = Column(Text, comment="外部权限系统资源标识（空=不判权；判权不可达时 fail-closed 隐藏）")
    # —— Xin台 B 版卡片语义列（kind 渲染分支；与 action/params 并存，前端按各自体系消费） ——
    subtitle = Column(Text, nullable=False, default="", server_default="", comment="副标题/功能描述（卡片名称下方一行）")
    kind = Column(String(16), nullable=False, default="task", server_default="task",
                  comment="卡片类型：link=页面跳转 / task=技能任务 / meeting=实时会议 / placeholder=功能占位")
    link_url = Column(Text, comment="跳转地址（/ 开头为 XuanPu 站内免登路径，否则外链）")
    skill_id = Column(Integer, comment="绑定的 XuanPu 技能 ID（可空，按名称回退匹配）")
    skill_name = Column(Text, comment="绑定的技能名称（ID 未配置时回退匹配用）")
    soon = Column(Boolean, nullable=False, default=False, server_default="false",
                  comment="置灰占位（二期/规划中，不可点击）")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")


# ---------------- 业务表（BusinessBase 三条红线） ----------------


class SysUser(BusinessBase):
    """用户表：本地账号 + 统一身份登录账号（登录走玄圃 SSO，本地 token 会话）。

    兼容两套映射：xuanpu_user_id（玄圃统一身份，launch 免登/权限判定用）与
    scms_user_id / org_id（SCMS 业务体系身份解析与 @ 组织选人用，未配置时降级）。
    """

    __tablename__ = "sys_users"

    user_id = Column(String(36), primary_key=True, default=gen_id, comment="用户 ID")
    username = Column(String(64), nullable=False, unique=True, comment="登录名")
    password_hash = Column(String(128), nullable=False, comment="口令哈希（统一身份登录建号为空）")
    display_name = Column(String(64), nullable=False, comment="显示名")
    role = Column(
        String(32), nullable=False,
        comment="角色：hq_finance=本部财务（含 admin）/ investee_finance=被投企业财务 / "
                "company_operator=各公司运营人员（任务内核流程）",
    )
    company = Column(String(64), comment="绑定的被投企业名称（被投财务/运营专属）")
    scms_user_id = Column(
        String(64), comment="SCMS 数字用户 ID（唯一；SCMS 出站按人透传/身份解析用，打通两套 id 体系）",
    )
    org_id = Column(String(36), comment="所属组织架构节点 ID（sys_org_units.org_id，@ 组织解析用）")
    # 账号来源: local=密码登录 / sso=统一身份登录 (JIT 建号, 无密码)
    auth_source = Column(String(16), nullable=False, default="local", server_default="local",
                         comment="账号来源：local=本地口令 / sso=统一身份登录（自动建号，无本地口令）")
    # XuanPu 用户绑定 (sso 建号时写入, 用于 launch 免登)
    xuanpu_user_id = Column(String(64), unique=True, comment="绑定的 XuanPu 用户 ID（站内系统免登跳转用）")

    __table_args__ = (
        Index("uq_sys_users_scms", "scms_user_id", unique=True),
        {"comment": "用户主数据：登录走玄圃 SSO（xuanpu_user_id 唯一绑定）；业务流程按 role+company "
                    "映射被投企业财务账号（派发待办用）；scms_user_id/org_id 供 SCMS 体系身份解析"
                    "（未配置时组织目录降级）"},
    )


class SysAuthToken(BusinessBase):
    """登录令牌表（本地会话 token 与 XuanPu 网关令牌双轨）。"""

    __tablename__ = "sys_auth_tokens"

    token = Column(String(128), primary_key=True, comment="访问令牌")
    user_id = Column(String(36), nullable=False, comment="所属用户 ID")
    expires_at = Column(DateTime(timezone=True), nullable=False, comment="过期时间")
    # XuanPu 网关 token (双轨鉴权: 有未过期值走 Bearer, 否则回落 X-User-Name)
    xuanpu_token = Column(Text, comment="XuanPu 网关令牌（未过期时走 Bearer 鉴权，否则回落 X-User-Name）")
    xuanpu_token_exp = Column(DateTime(timezone=True), comment="XuanPu 网关令牌过期时间")

    __table_args__ = {
        "comment": "登录令牌表（本地会话 token 与 XuanPu 网关令牌双轨）：deps.current_user 校验本地 token；"
                   "xuanpu_token 供网关调用透传用户身份"
    }


class BizTask(BusinessBase):
    """任务派发单：本部财务发起业务动作的主表。"""

    __tablename__ = "biz_tasks"

    task_id = Column(String(36), primary_key=True, default=gen_id, comment="任务 ID")
    parent_task_id = Column(
        String(36), comment="父任务 ID（递归内核：数据填报→风险应对方案→风险应对项 的任务树）",
    )
    template_key = Column(
        String(64), comment="任务模板键（小流程声明引用，随插件发布）；空=通用待办（无表单内容）；动态模板用 dyn_<id> 前缀",
    )
    dynamic_template_id = Column(
        String(64), nullable=True,
        comment="动态填报模板 ID（关联 dynamic_template.template_id，类型 varchar 避免跨库 FK）；非空时待办走动态 HTML 填报",
    )
    scene = Column(
        String(32), nullable=False,
        comment="场景：risk_fill/cash_guarantee/kpi_fill/ms_feedback/lamp_adjust/report/generic",
    )
    title = Column(String(255), nullable=False, comment="任务标题")
    dispatcher_id = Column(String(36), nullable=False, comment="派发人（本部财务）用户 ID")
    payload = Column(
        JSONB, nullable=False, default=dict,
        comment="场景上下文引用 JSONB（batch_id/form_id/feedback_id 等）",
    )
    period = Column(String(32), comment="归属期（YYYY-MM）；期间门控依据（自然月锁定）")
    status = Column(
        String(16), nullable=False, default="open",
        comment="状态：open=进行中 / ended=已结束（全部完成或强制结束）",
    )
    related_system = Column(String(128), comment="关联系统名（需求B临时看板展示，无则空）")
    review_required = Column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="是否需要派发人审核：true=承办人提交后进 submitted 待审（两段式）；"
                "false=提交即完成（兼容存量流程）",
    )
    due = Column(DateTime(timezone=True), comment="截止时间（催办天数基数、逾期统计）")
    end_policy = Column(
        String(16), nullable=False, default="all_done", server_default="all_done",
        comment="结束策略：all_done=全部完成后可结束 / manual=可强制结束（未完成留痕）",
    )
    ended_at = Column(DateTime(timezone=True), comment="结束时间")
    ended_by = Column(String(36), comment="结束操作人 ID")
    end_note = Column(Text, comment="结束说明（强制结束时的留痕说明）")
    spawn_meta = Column(
        JSONB, nullable=False, default=dict,
        comment="派生元数据 JSONB：{trigger: dispatch|alarm_embedded|manual, "
                "anchor: {company, indicator_idx}, prev_task_id, reuse_mode: same|adjusted}",
    )

    __table_args__ = (
        Index("idx_tasks_parent", "parent_task_id"),
        Index("idx_tasks_tpl_period", "template_key", "period"),
        {"comment": "任务派发单（内核·派发层）：固定四状态「派发-处理-审核-结束/重新填报」的任务树节点；"
                    "parent_task_id 递归承载子流程，template_key 引用小流程声明（业务差异全部下放模板）"},
    )


class BizTodo(BusinessBase):
    """待办明细：一个 task 派发给多个承办人各一行。"""

    __tablename__ = "biz_todos"

    todo_id = Column(String(36), primary_key=True, default=gen_id, comment="待办 ID")
    task_id = Column(String(36), nullable=False, comment="所属任务 ID")
    assignee_id = Column(String(36), nullable=False, comment="承办人用户 ID")
    kind = Column(
        String(32), nullable=False, default="action",
        comment="类型：action=业务动作 / review=审批 / na_confirm=不涉及确认 / feedback_review=反馈复核",
    )
    scene = Column(String(32), nullable=False, default="generic", comment="场景（同 biz_tasks.scene 冗余）")
    title = Column(String(255), nullable=False, comment="待办标题")
    sub = Column(String(255), nullable=False, default="", comment="副标题/说明")
    status = Column(
        String(32), nullable=False, default="pending",
        comment="内核状态机：pending=待处理 / submitted=待审核 / returned=已退回（数据保留可重交） / "
                "completed=已完成 / na_pending=待确认（不涉及） / na_closed=已不涉及；"
                "派生态 overdue=未填报（期间过期×pending|returned，查询时计算不落库）",
    )
    lamp = Column(String(1), comment="随待办携带的红黄绿灯：r/y/g")
    ref = Column(
        JSONB, nullable=False, default=dict,
        comment="前端跳转引用 JSONB（batch_id/company/report_id/form_id/feedback_id）",
    )
    payload = Column(
        JSONB, nullable=False, default=dict,
        comment="表单产出 JSONB（内容槽·权威源）：仅 pending/returned 可写（提交后对承办人锁定）；"
                "退回不清空——「已退回但填写的数据保留」",
    )
    completion_text = Column(Text, comment="完成说明（承办人提交时填写，需求B「已完成」卡）")
    feedback_text = Column(Text, comment="反馈文本（feedback_review 场景承办人提交）")
    return_reason = Column(Text, comment="审核退回说明（审核方填写，处理人侧展示）")
    na_reason = Column(Text, comment="不涉及原因（承办人标记 N/A 时填写）")
    na_comment = Column(Text, comment="不涉及驳回意见（派发人确认时填写）")
    redispatch_reason = Column(Text, comment="再次派发说明（不涉及驳回/追加再派发时填写）")
    lamp_agg = Column(
        JSONB, nullable=False, default=dict, server_default="{}",
        comment="灯色聚合计数 JSONB {r,y,g}（submit 时按 payload 物化，看板统计用）",
    )
    remind_count = Column(Integer, nullable=False, default=0, server_default="0", comment="催办次数")
    last_reminded_at = Column(DateTime(timezone=True), comment="最近催办时间")
    due = Column(DateTime(timezone=True), comment="截止时间（逾期统计用）")
    submitted_at = Column(DateTime(timezone=True), comment="最近提交时间")
    returned_at = Column(DateTime(timezone=True), comment="最近退回时间")
    completed_at = Column(DateTime(timezone=True), comment="完成时间")
    na_at = Column(DateTime(timezone=True), comment="最近不涉及操作时间")
    session_id = Column(Uuid, comment="处理人操作所在的 AI 会话 ID（历史对话记录锚点）")

    __table_args__ = (
        Index("idx_todos_assignee", "assignee_id", "status"),
        Index("idx_todos_task_status", "task_id", "status"),
        Index("idx_todos_payload", "payload", postgresql_using="gin"),
        {"comment": "待办明细（内核·处理/审核层）：状态机唯一权威；payload 为表单内容槽，"
                    "退回保留、提交锁定；两段式审核（submit→review）由 task.review_required 决定"},
    )


class UserNotification(BusinessBase):
    """用户消息中心通知：任务流转产生，按接收人维护已读状态。"""

    __tablename__ = "user_notifications"

    notification_id = Column(String(36), primary_key=True, default=gen_id, comment="通知 ID")
    recipient_id = Column(String(36), nullable=False, comment="接收用户 ID")
    actor_id = Column(String(36), nullable=False, comment="触发通知的用户 ID")
    category = Column(String(32), nullable=False, comment="todo_remind/todo_submitted/todo_completed/todo_returned")
    title = Column(String(255), nullable=False, comment="通知标题")
    body = Column(Text, nullable=False, default="", server_default="", comment="通知正文")
    todo_id = Column(String(36), nullable=False, comment="关联待办 ID")
    task_id = Column(String(36), nullable=False, comment="关联任务 ID")
    is_read = Column(Boolean, nullable=False, default=False, server_default="false", comment="是否已读")
    read_at = Column(DateTime(timezone=True), comment="阅读时间")

    __table_args__ = (
        Index("idx_notifications_recipient", "recipient_id", "is_read", "created_at"),
        Index("idx_notifications_todo", "todo_id", "created_at"),
        {"comment": "消息中心通知：保存催办、提交完成、审核完成和退回消息，支持离线查看与已读状态"},
    )


class TaskArtifact(BusinessBase):
    """任务交付物（附件）——内容槽产出之一。"""

    __tablename__ = "task_artifacts"

    artifact_id = Column(String(36), primary_key=True, default=gen_id, comment="交付物 ID")
    todo_id = Column(String(36), nullable=False, comment="所属待办 ID（完成交付物/审批方案文档）")
    task_id = Column(String(36), comment="所属任务 ID（任务级附件，可空）")
    file_name = Column(String(255), nullable=False, comment="原始文件名")
    storage_key = Column(String(255), nullable=False, comment="存储键（本地目录/对象存储路径）")
    mime_type = Column(String(128), comment="MIME 类型")
    file_size = Column(BigInteger, comment="文件大小（字节）")
    sha256 = Column(String(64), comment="内容摘要（去重/校验）")
    kb_included = Column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="是否纳入知识库（需求B审核弹窗展示的纳入状态）",
    )
    uploaded_by = Column(String(36), comment="上传人 ID")

    __table_args__ = (
        Index("idx_artifacts_todo", "todo_id"),
        {"comment": "任务交付物：处理产出附件（完成交付件/方案文档），kb_included 标记知识库纳入；"
                    "文件本体存外部存储，本表只存元数据"},
    )


class TaskLampLog(BusinessBase):
    """灯色人工调整流水（append-only，永不变更 payload 原灯）。"""

    __tablename__ = "task_lamp_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    todo_id = Column(String(36), nullable=False, comment="调灯目标待办 ID")
    field_path = Column(
        String(128), nullable=False, default="overall",
        comment="灯色字段路径：overall=整体 / items[N].lamp=明细项（JSONPath 风格）",
    )
    old_lamp = Column(String(1), comment="原灯色 r/y/g")
    new_lamp = Column(String(1), nullable=False, comment="新灯色 r/y/g")
    reason = Column(Text, nullable=False, default="", comment="调整原因")
    operator_id = Column(String(64), nullable=False, default="", comment="操作人 ID（计财部专属权限）")

    __table_args__ = (
        Index("idx_lamp_log_todo", "todo_id", "id"),
        {"comment": "灯色人工调整流水：审核方在任意状态调灯时追加，append-only 追溯；"
                    "展示时以流水最新值覆盖原灯，原值永不修改"},
    )


class TaskRemark(BusinessBase):
    """任务/待办备注（append-only；期间锁定后唯一可用操作之一）。"""

    __tablename__ = "task_remarks"

    remark_id = Column(String(36), primary_key=True, default=gen_id, comment="备注 ID")
    task_id = Column(String(36), comment="锚定任务 ID（与 todo_id 二选一）")
    todo_id = Column(String(36), comment="锚定待办 ID（与 todo_id 二选一）")
    content = Column(Text, nullable=False, comment="备注内容")
    author_id = Column(String(64), nullable=False, default="", comment="备注人 ID")

    __table_args__ = (
        Index("idx_remarks_todo", "todo_id"),
        Index("idx_remarks_task", "task_id"),
        {"comment": "任务/待办备注：各状态的「添加备注」按钮落此处，append-only；"
                    "期间锁定（进入下一自然月）后查看与备注仍可用"},
    )


class SysOrgUnit(BusinessBase):
    """组织架构树（@ 组织解析用）。"""

    __tablename__ = "sys_org_units"

    org_id = Column(String(36), primary_key=True, default=gen_id, comment="组织节点 ID")
    parent_org_id = Column(String(36), comment="父节点 ID（根为空）")
    name = Column(String(128), nullable=False, comment="组织名称")

    __table_args__ = {
        "comment": "组织架构树：AI 派发 @组织 的解析目录（parent_org_id 构成树）"
    }


class SysUserGroup(BusinessBase):
    """用户组（@ 用户组解析用）。"""

    __tablename__ = "sys_user_groups"

    group_id = Column(String(36), primary_key=True, default=gen_id, comment="用户组 ID")
    name = Column(String(128), nullable=False, comment="用户组名称")

    __table_args__ = {
        "comment": "用户组：AI 派发 @用户组 的解析目录"
    }


class SysUserGroupMember(BusinessBase):
    """用户组成员关系。"""

    __tablename__ = "sys_user_group_members"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    group_id = Column(String(36), nullable=False, comment="用户组 ID")
    user_id = Column(String(36), nullable=False, comment="成员用户 ID")

    __table_args__ = (
        Index("uq_group_member", "group_id", "user_id", unique=True),
        {"comment": "用户组成员关系：(group_id, user_id) 唯一"},
    )


class RiskFillBatch(BusinessBase):
    """风险预警财务指标填报批次。"""

    __tablename__ = "risk_fill_batches"

    batch_id = Column(String(36), primary_key=True, default=gen_id, comment="批次 ID")
    period = Column(String(32), nullable=False, comment="归属期（YYYY-MM）")
    dispatcher_id = Column(String(36), nullable=False, comment="派发人用户 ID")
    status = Column(
        String(16), nullable=False, default="collecting",
        comment="状态：collecting=收集中 / done=完成",
    )

    __table_args__ = {
        "comment": "风险预警财务指标填报批次：本部财务按归属期下发，同时建 biz_task "
                   "并为每家被投企业生成填报单与 action 待办"
    }


class RiskFillReport(BusinessBase):
    """风险填报单（批次×企业）。"""

    __tablename__ = "risk_fill_reports"

    report_id = Column(String(36), primary_key=True, default=gen_id, comment="填报单 ID")
    batch_id = Column(String(36), nullable=False, comment="所属批次 ID")
    company = Column(String(64), nullable=False, comment="被投企业名称")
    status = Column(
        String(16), nullable=False, default="unfilled",
        comment="状态：unfilled=未填 / filled=已提交 / reviewed=已审批（已审批不可修改）",
    )
    lamp_r = Column(Integer, nullable=False, default=0, comment="红灯指标数（16 项明细汇总）")
    lamp_y = Column(Integer, nullable=False, default=0, comment="黄灯指标数")
    lamp_g = Column(Integer, nullable=False, default=0, comment="绿灯指标数")

    __table_args__ = (
        Index("idx_risk_reports_batch", "batch_id", "company"),
        {"comment": "风险填报单（批次×企业）：unfilled→filled（生成 review 待办）→reviewed 审批流，"
                    "驳回退回 unfilled；灯色计数按明细行保存后重算"},
    )


class RiskFillItem(BusinessBase):
    """风险填报名细行（16 项固定指标）。"""

    __tablename__ = "risk_fill_items"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    report_id = Column(String(36), nullable=False, comment="所属填报单 ID")
    idx = Column(Integer, nullable=False, comment="指标固定序号 1-16")
    name = Column(String(128), nullable=False, comment="指标名称")
    lamp = Column(String(1), nullable=False, default="g", comment="单项灯色：r/y/g（默认 g）")
    fields = Column(
        JSONB, nullable=False, default=list,
        comment="填报字段 JSONB：[{k:字段名, v:填报值, pf:true=系统预填只读}]，保存时 pf 保留原值",
    )

    __table_args__ = (
        Index("idx_risk_items_report", "report_id", "idx"),
        {"comment": "风险填报名细行：按 16 项固定指标模板（主营业务收入/归母净利润/资产负债率/现金保障倍数等）"
                    "建单时预生成"},
    )


class CashGuaranteeReport(BusinessBase):
    """现金保障倍数月度填报单。"""

    __tablename__ = "cash_guarantee_reports"

    form_id = Column(String(36), primary_key=True, default=gen_id, comment="填报单 ID")
    company = Column(String(64), nullable=False, comment="被投企业名称")
    period = Column(String(32), nullable=False, comment="归属期（YYYY-MM）")
    avail_cash = Column(Float, nullable=False, default=0, comment="可用货币资金（万元）")
    pooled_fund = Column(Float, nullable=False, default=0, comment="资金集中金额（万元）")
    avail_credit = Column(Float, nullable=False, default=0, comment="可用授信（万元）")
    monthly_outflow = Column(Float, nullable=False, default=0, comment="月均支出（万元）")
    ratio = Column(Float, nullable=False, default=0, comment="现金保障倍数=（前三项之和）/月均支出")
    lamp = Column(String(1), nullable=False, default="g", comment="灯色：r/y/g")
    status = Column(
        String(16), nullable=False, default="draft",
        comment="状态：draft=草稿 / submitted=已提交（生成 review 待办）/ reviewed=已审批（驳回退 draft）",
    )
    session_id = Column(Uuid, comment="发起本次填报的对话会话 ID（AI 路径联动，可空）")

    __table_args__ = {
        "comment": "现金保障倍数月度填报单（企业×期）：ratio=(可用资金+集中资金+可用授信)/月均支出 自动计算，"
                   "≤3 红 / ≤6 黄 / >6 绿；页面或 AI 对话组件（cash-guarantee-form）均可发起，建单自动预填上期值"
    }


class KpiBatch(BusinessBase):
    """经营者考核批次。"""

    __tablename__ = "kpi_batches"

    batch_id = Column(String(36), primary_key=True, default=gen_id, comment="批次 ID")
    period = Column(String(32), nullable=False, comment="归属期（YYYY-MM）")
    dispatcher_id = Column(String(36), nullable=False, comment="派发人用户 ID")
    status = Column(String(16), nullable=False, default="collecting", comment="状态：collecting=收集中")

    __table_args__ = {
        "comment": "经营者考核批次：本部财务按归属期发起，为全部被投企业按 4 指标模板生成指标行并派 action 待办"
    }


class KpiIndicator(BusinessBase):
    """考核指标行（批次×企业，4 项固定模板）。"""

    __tablename__ = "kpi_indicators"

    indicator_id = Column(String(36), primary_key=True, default=gen_id, comment="指标行 ID")
    batch_id = Column(String(36), nullable=False, comment="所属批次 ID")
    company = Column(String(64), nullable=False, comment="被投企业名称")
    dim = Column(String(32), nullable=False, default="", comment="维度：财务/转型/合规")
    name = Column(String(128), nullable=False, comment="指标名称")
    kpi_type = Column(String(32), nullable=False, default="定量", comment="指标类型：定量/定性")
    content = Column(Text, nullable=False, default="", comment="企业填报内容")
    base_score = Column(String(16), nullable=False, default="", comment="基准分（字符串存储）")
    max_score = Column(String(16), nullable=False, default="", comment="满分（字符串存储）")
    status = Column(
        String(16), nullable=False, default="unfilled",
        comment="状态：unfilled=未填 / filled=已提交 / reviewed=已审批（驳回整体退 unfilled）",
    )

    __table_args__ = (
        Index("idx_kpi_ind_batch", "batch_id", "company"),
        {"comment": "考核指标行（批次×企业，4 项固定模板）：营业收入/净利润（定量 30+30）+"
                    "数字化转型进度/合规经营（定性 20+20）"},
    )


class KpiMilestone(BusinessBase):
    """里程碑拆分计划（企业对定性指标拆出的里程碑行）。"""

    __tablename__ = "kpi_milestones"

    milestone_id = Column(String(36), primary_key=True, default=gen_id, comment="里程碑 ID")
    indicator_id = Column(String(36), nullable=False, comment="所属指标行 ID")
    content = Column(Text, nullable=False, default="", comment="里程碑内容")
    plan_date = Column(String(32), nullable=False, default="", comment="计划完成日期（字符串）")
    material = Column(String(255), nullable=False, default="", comment="证明材料说明")
    status = Column(String(16), nullable=False, default="pending", comment="状态（默认 pending）")

    __table_args__ = (
        Index("idx_kpi_ms_indicator", "indicator_id"),
        {"comment": "里程碑拆分计划：企业对（定性）指标拆出的里程碑行，是「里程碑反馈」的派发对象；"
                    "SCMS 里程碑审核卡直连外部系统不落本表"},
    )


class KpiMsFeedback(BusinessBase):
    """里程碑进展反馈单（与 kpi_milestones 一对一）。"""

    __tablename__ = "kpi_ms_feedbacks"

    feedback_id = Column(String(36), primary_key=True, default=gen_id, comment="反馈单 ID")
    milestone_id = Column(String(36), nullable=False, comment="对应里程碑 ID")
    batch_id = Column(String(36), nullable=False, default="", comment="派发时所在考核批次 ID（冗余）")
    company = Column(String(64), nullable=False, comment="被投企业名称（冗余）")
    status = Column(
        String(32), nullable=False, default="进行中",
        comment="进展状态（中文枚举）：未开始/进行中/已完成/滞后",
    )
    progress = Column(Integer, nullable=False, default=0, comment="进度百分比 0-100")
    actual_date = Column(String(32), comment="实际完成日期（可空）")
    lamp = Column(String(1), nullable=False, default="g", comment="灯色：r/y/g")
    status_note = Column(Text, comment="情况说明")
    review_status = Column(
        String(16), nullable=False, default="draft",
        comment="审批状态：draft=草稿 / submitted=已提交（生成 review 待办）/ reviewed=已审批（驳回退 draft）",
    )

    __table_args__ = {
        "comment": "里程碑进展反馈单：对最新批次全部在列里程碑各生成一行，企业填报进展后走审批流；"
                   "冗余 batch_id/company 便于按企业过滤"
    }


class KpiLampAdjustment(BusinessBase):
    """考核指标亮灯人工调整流水（append-only）。"""

    __tablename__ = "kpi_lamp_adjustments"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    company = Column(String(64), nullable=False, comment="被投企业名称")
    indicator_name = Column(String(128), nullable=False, comment="指标名称")
    old_lamp = Column(String(1), nullable=False, comment="原灯色 r/y/g（取最近同名反馈的灯，无则 g）")
    new_lamp = Column(String(1), nullable=False, comment="新灯色 r/y/g")
    reason = Column(Text, nullable=False, default="", comment="调整原因")
    operator = Column(String(64), nullable=False, default="", comment="操作人 ID")

    __table_args__ = {
        "comment": "考核指标亮灯人工调整流水：本部财务调灯时追加，append-only 追溯灯色变更历史"
    }


class PitReport(BusinessBase):
    """投后管理（Post-Investment）报告生成任务。"""

    __tablename__ = "pit_reports"

    report_id = Column(String(36), primary_key=True, default=gen_id, comment="报告 ID（传入可幂等复用防重复建单）")
    company_ids = Column(JSONB, nullable=False, default=list, comment="被投企业名称数组 JSONB（用户多选）")
    period = Column(String(32), nullable=False, comment="归属期")
    outline = Column(JSONB, nullable=False, default=list, comment="大纲小节标题数组 JSONB（≤6 项）")
    content = Column(Text, nullable=False, default="", comment="正文 markdown（按「## 小节」逐节追加）")
    status = Column(
        String(16), nullable=False, default="outlining",
        comment="状态：outlining=生成大纲中 / draft=逐节生成中 / done=完成",
    )

    __table_args__ = {
        "comment": "投后管理（PIT=Post-Investment）报告生成任务：AI 报告确认组件或 API 触发，"
                   "后台线程先大纲后逐节生成正文；失败时 content 写「生成失败：…」留痕"
    }


class HtmlReport(BusinessBase):
    """AI 生成 HTML 报告（对话工具 generate_html_report 的产物）。"""

    __tablename__ = "html_reports"

    report_id = Column(String(36), primary_key=True, default=gen_id, comment="报告 ID")
    session_id = Column(Uuid, comment="来源会话 ID（非 UUID 会话标识容错置空）")
    title = Column(String(256), nullable=False, comment="报告标题")
    content = Column(Text, nullable=False, comment="完整 HTML 文档（对象存储为权威副本，此处兜底）")
    __table_args__ = {
        "comment": "AI 生成 HTML 报告：agent 工具/技能脚本落盘，正文同时写对象存储（MinIO/本地降级），"
                   "读取走 GET /html-reports/{report_id} 直出 + 按 source 分级 CSP"
    }


class PptReport(BusinessBase):
    """AI 生成 PPT 报告（对话工具 generate_fin_risk_report 的产物）。"""

    __tablename__ = "ppt_reports"

    report_id = Column(String(36), primary_key=True, default=gen_id, comment="报告 ID")
    session_id = Column(Uuid, comment="来源会话 ID（非 UUID 会话标识容错置空）")
    title = Column(String(256), nullable=False, comment="报告标题")
    period = Column(String(32), nullable=False, default="", comment="归属期（YYYY-MM）")
    file_name = Column(String(255), nullable=False, comment="下载文件名（含 .pptx）")
    spec = Column(JSONB, nullable=False, default=dict, comment="PptSpec 中间表示 JSON（留档可复算/审计）")

    __table_args__ = {
        "comment": "AI 生成 PPT 报告：agent 工具同步渲染（python-pptx），二进制只存对象存储"
                   "（MinIO/本地降级，无 DB 回退），下载走 GET /ppt-reports/{report_id}/download"
    }


class KbSource(BusinessBase):
    """知识库目录树。"""

    __tablename__ = "kb_sources"

    kb_id = Column(String(36), primary_key=True, comment="知识库节点 ID")
    name = Column(String(128), nullable=False, comment="节点名称")
    parent_id = Column(String(36), comment="父节点 ID（根为空）")
    kb_type = Column(
        String(16), nullable=False, default="internal",
        comment="类型：internal=内部 / external=外部",
    )
    mcp_ref = Column(String(128), comment="MCP 知识库引用标识（占位，当前未使用）")

    __table_args__ = {
        "comment": "知识库目录树：前端知识库选择器数据源，parent_id 构成树（seed 预置企业/部门/项目/外部/个人）；"
                   "检索走全局 kb_mcp_url，kb_id 仅作检索参数透传"
    }


class SkillTemplate(BusinessBase):
    """技能模版（动态维护）：定位分类/id/名称/排序号 + 模版内容 JSON（工具调用地址等）。"""

    __tablename__ = "skill_templates"

    template_id = Column(String(36), primary_key=True, default=gen_id, comment="模版 ID")
    skill_key = Column(
        String(64), nullable=False,
        comment="归属技能：post_report/fin_risk_report/info_fill/kpi_fill/ms_feedback/lamp_adjust/task_stats/generic_dispatch",
    )
    category = Column(String(64), nullable=False, default="", comment="分类")
    name = Column(String(128), nullable=False, comment="模版名称")
    sort_no = Column(Integer, nullable=False, default=0, comment="排序号")
    content = Column(
        JSONB, nullable=False, default=dict,
        comment="模版内容 JSONB：{tool:工具调用名, prompt:触发话术, file_type:docx/pptx, status:dev=开发中}",
    )
    enabled = Column(Boolean, nullable=False, default=True, server_default="true", comment="是否上架")

    __table_args__ = (
        Index("idx_skill_tpl_skill", "skill_key", "sort_no"),
        {"comment": "技能模版：技能（=一组工具的命名集合）下的具体模版；首次访问按 DEFAULT_TEMPLATES 懒播种，"
                    "之后可动态增改"},
    )


class UserSkill(BusinessBase):
    """用户技能启用配置：按用户 ID 加载工具；无记录行 = 全量（API 兼容），
    前端首次拉取目录时懒写入默认 3 核心技能启用行。"""

    __tablename__ = "user_skills"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    user_id = Column(String(36), nullable=False, comment="用户 ID")
    skill_key = Column(String(64), nullable=False, comment="技能键（同 skill_templates.skill_key）")
    enabled = Column(Boolean, nullable=False, default=True, server_default="true", comment="是否启用")

    __table_args__ = (
        Index("uq_user_skills_user_key", "user_id", "skill_key", unique=True),
        {"comment": "用户技能启用配置：按用户控制 agent 可用工具集；(user_id, skill_key) 唯一；"
                    "无记录行=全量工具（API 兼容），前端首拉目录时懒写入默认集"},
    )


class HeroCard(BusinessBase):
    """用户自定义卡片（预留：Xin语页个人快捷卡，链接/技能两类，按用户维度，逻辑删除）。"""

    __tablename__ = "hero_cards"
    __table_args__ = (
        Index("idx_hero_cards_user", "user_id", "is_delete"),
        {"comment": "用户自定义卡片（链接/技能，按用户维度，逻辑删除）"},
    )

    id = Column(String(36), primary_key=True, default=gen_id, comment="卡片 ID（UUID）")
    user_id = Column(String(36), nullable=False, index=True, comment="所属用户 ID")
    name = Column(String(128), nullable=False, comment="卡片名称")
    kind = Column(String(16), nullable=False, default="link", comment="卡片类型：link=链接 / skill=技能")
    link_url = Column(Text, comment="跳转地址（link 卡）")
    skill_id = Column(Integer, comment="绑定的技能 ID（skill 卡）")
    skill_name = Column(Text, comment="绑定的技能名称（skill 卡）")
    sort = Column(Integer, nullable=False, default=0, server_default="0", comment="排序值（升序）")


# ---------------- 声纹识别（说话人实名，services/voiceprint.py） ----------------


class UserBiometricConsent(BusinessBase):
    """生物信息采集告知同意存证（PIPL 单独同意要求，一人一类型一条）。"""

    __tablename__ = "user_biometric_consents"

    id = Column(String(36), primary_key=True, default=gen_id, comment="存证 ID（UUID）")
    user_id = Column(String(64), nullable=False, comment="用户 ID")
    consent_type = Column(String(32), nullable=False, default="voiceprint", comment="同意类型：voiceprint=声纹")
    protocol_version = Column(String(16), nullable=False, comment="同意的协议版本（BIOMETRIC_PROTOCOL_VERSION）")
    agreed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="同意时间")

    __table_args__ = (
        Index("ux_biometric_consent_user_type", "user_id", "consent_type", unique=True),
        {"comment": "生物信息采集告知同意存证：重复同意覆盖更新版本与时间"},
    )


class UserVoiceprint(BusinessBase):
    """用户声纹：无感采样累积（samples）→ 达标后质心（embedding）实名注册。"""

    __tablename__ = "user_voiceprints"

    id = Column(String(36), primary_key=True, default=gen_id, comment="记录 ID（UUID）")
    user_id = Column(String(64), nullable=False, comment="用户 ID")
    enrolled = Column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="是否已注册（false 期间静默累积 samples）",
    )
    samples = Column(
        JSONB, nullable=False, default=list, server_default="[]",
        comment="达标前缓存向量数组 [{vec:[float...], seconds:float}]（最多 MAX_SAMPLES 条）",
    )
    sample_seconds = Column(Float, nullable=False, default=0.0, server_default="0", comment="累积采样秒数")
    embedding = Column(JSONB, nullable=True, comment="注册质心向量（L2 归一化 float 数组）")
    dimension = Column(Integer, nullable=False, default=0, server_default="0", comment="向量维度")

    __table_args__ = (
        Index("ux_voiceprint_user", "user_id", unique=True),
        {"comment": "用户声纹：一人一条；累积达标自动写 embedding 转实名"},
    )


class MeetingSpeakerAlias(BusinessBase):
    """会议内未注册说话人编号映射（本场在线聚类，同人同号，仅本场有效不回溯）。"""

    __tablename__ = "meeting_speaker_aliases"

    id = Column(String(36), primary_key=True, default=gen_id, comment="记录 ID（UUID）")
    meeting_id = Column(String(64), nullable=False, comment="所属会议 ID")
    speaker_label = Column(String(32), nullable=False, comment="编号标签（说话人1/说话人2…）")
    embedding = Column(JSONB, nullable=False, comment="该编号代表向量（首段质心，后续命中均值更新）")
    sample_seconds = Column(Float, nullable=False, default=0.0, server_default="0", comment="该编号累积秒数（质心加权用）")
    resolved_user_id = Column(String(64), nullable=True, comment="实名合并：上传者注册成功后绑定的用户 ID（命中该编号直接显示实名）")

    __table_args__ = (
        Index("ix_meeting_alias_mtg", "meeting_id"),
        UniqueConstraint("meeting_id", "speaker_label", name="uq_meeting_alias_mtg_label"),
        {"comment": "会议内未注册说话人编号：embedding 在线聚类；命中注册声纹则不走本表直接实名；注册成功后相似编号回绑实名并回填字幕"},
    )


# ---------------- src 侧（玄圃集成）自有模型 ----------------


class TaskRecord(BusinessBase):
    """历史任务（左侧任务抽屉）：Xin语对话记录（chat，引用 platform_sessions）与 Xin台 AI 能力
    执行记录（exec）统一树；kind=folder 为用户文件夹（scope 标明归属 chat / exec 列表）。逻辑删除。"""

    __tablename__ = "task_records"
    __table_args__ = (Index("idx_task_records_user", "user_id", "scope", "kind"),
                      {"comment": "历史任务（Xin语对话记录与 Xin台执行记录的统一树，含用户文件夹，逻辑删除）"})

    id = Column(String(36), primary_key=True, default=gen_id, comment="记录 ID（UUID）")
    user_id = Column(String(36), nullable=False, index=True, comment="所属用户 ID")
    kind = Column(String(16), nullable=False, comment="记录类型：chat=对话 / exec=执行记录 / folder=文件夹")
    scope = Column(String(16), nullable=False, default="chat", server_default="chat",
                   comment="所属列表：chat=Xin语对话 / exec=Xin台执行")
    title = Column(String(255), nullable=False, default="", comment="名称（对话标题 / 执行标题 / 文件夹名）")
    status = Column(String(16), nullable=False, default="", comment="执行状态：running=运行中 / success=成功 / failed=失败 / stopped=已停止")
    ref_id = Column(String(64), comment="引用 ID（chat=会话 ID，exec=来源卡片标识）")
    detail = Column(JSONB, nullable=False, default=dict, comment="扩展信息（JSON，如执行输出摘要、会议时长）")
    parent_id = Column(String(36), comment="上级文件夹 ID（顶层为 NULL）")
    sort = Column(Integer, nullable=False, default=0, server_default="0", comment="同层排序值（降序展示，大者在前）")


class SysUserModelPref(BusinessBase):
    """用户个人模型偏好表（按模型通道覆盖 .docker.env 默认，空字段回退默认值）。"""

    __tablename__ = "sys_user_model_prefs"
    __table_args__ = (
        Index("uq_user_model_pref", "user_id", "channel", unique=True),
        {"comment": "用户个人模型偏好（main/small/coder/vision 通道级覆盖；未设置的通道走 .docker.env 默认）"},
    )

    pref_id = Column(String(36), primary_key=True, default=gen_id, comment="偏好 ID（UUID）")
    user_id = Column(String(36), nullable=False, comment="所属用户 ID（sys_users.user_id）")
    channel = Column(String(16), nullable=False, comment="模型通道：main=主推理 / small=轻量快推 / coder=代码生成 / vision=视觉")
    api_url = Column(String(512), nullable=False, server_default="", comment="网关地址覆盖（空=用系统默认）")
    api_key = Column(String(256), nullable=False, server_default="", comment="网关密钥覆盖（空=用系统默认）")
    model = Column(String(128), nullable=False, server_default="", comment="模型名覆盖（空=用系统默认）")
    max_tokens = Column(Integer, nullable=False, server_default="0", comment="单次生成输出上限覆盖（0=用系统默认）")
