"""数据库中文注释：为全部 25 张表写入表级 / 列级 COMMENT

对应 models.py 中的 comment 参数（本文件为快照，与模型保持一致）。
仅 COMMENT ON 语句，不改动表结构与数据，幂等可重放。

Revision ID: 0005_db_chinese_comments
Revises: 0004_task_records_and_cards
Create Date: 2026-09-08
"""
from alembic import op

revision = "0005_db_chinese_comments"
down_revision = "0004_task_records_and_cards"
branch_labels = None
depends_on = None

# 业务表公共字段（BusinessBase）
_COMMON = {
    "is_delete": "逻辑删除标记（true=已删除）",
    "created_by": "创建人",
    "created_at": "创建时间",
    "updated_by": "最后更新人",
    "updated_at": "最后更新时间（数据库触发器维护）",
}

# 继承 BusinessBase 的表（公共字段由 _COMMON 注入）
_BUSINESS = frozenset((
    "task_records", "hero_cards", "sys_users", "sys_auth_tokens", "biz_tasks",
    "biz_todos", "risk_fill_batches", "risk_fill_reports", "risk_fill_items",
    "cash_guarantee_reports", "kpi_batches", "kpi_indicators", "kpi_milestones",
    "kpi_ms_feedbacks", "kpi_lamp_adjustments", "pit_reports", "kb_sources",
    "skill_templates", "user_skills",
))

# {表名: {None: 表注释, 列名: 列注释}}；业务表不重复写公共五列
_TABLES: dict[str, dict[str | None, str]] = {
    "platform_sessions": {
        None: "AI 对话会话主表（Xin语对话的会话头，事件明细见 platform_session_events）",
        "session_id": "会话 ID（UUID）",
        "user_id": "所属用户 ID",
        "title": "会话标题",
        "domain": "业务域（如 finance）",
        "plugin_set": "会话绑定的插件集合（JSON 数组）",
        "plugin_set_hash": "插件集合哈希（变更检测用）",
        "status": "状态：active=进行中 / archived=已归档",
        "created_at": "创建时间",
        "updated_at": "最后更新时间",
    },
    "platform_session_events": {
        None: "会话事件流（用户/助手/工具事件明细，append-only）",
        "session_id": "所属会话 ID",
        "seq": "会话内事件序号（0 起连续）",
        "type": "事件类型（如 user / assistant / tool）",
        "time": "事件时间",
        "data": "事件内容（JSON，按类型异构）",
        "ignorable": "回放时是否可忽略",
        "turn": "对话轮次",
    },
    "platform_compensations": {
        None: "插件副作用补偿任务（执行失败/中断后的回滚动作队列）",
        "id": "自增主键",
        "session_id": "所属会话 ID",
        "event_seq": "触发补偿的事件序号",
        "plugin_name": "插件名",
        "action": "补偿动作",
        "payload": "补偿参数（JSON）",
        "status": "状态：pending=待执行 / done=完成 / failed=失败",
        "retry_count": "已重试次数",
        "created_at": "创建时间",
    },
    "platform_operation_logs": {
        None: "系统级操作日志（append-only，所有读写操作留痕）",
        "id": "自增主键",
        "occurred_at": "发生时间",
        "user_id": "操作用户 ID",
        "session_id": "关联会话 ID",
        "channel": "渠道（如 page / api）",
        "actor": "操作主体（如 page:login）",
        "plugin_name": "插件名（插件操作时记录）",
        "entity": "操作实体（表名/对象名）",
        "operation": "操作类型：select / insert / update",
        "record_key": "操作记录的主键",
        "detail": "操作明细（JSON）",
        "client_ip": "客户端 IP",
        "entry_point": "入口（如 POST /api/v1/auth/login）",
        "request_id": "请求 ID（链路追踪）",
    },
    "platform_projections": {
        None: "会话投影状态（事件流折叠后的当前视图，按投影键存取）",
        "session_id": "所属会话 ID",
        "key": "投影键（如 todo_state）",
        "state_version": "投影状态版本",
        "seq": "已投影至的事件序号",
        "value": "投影值（JSON）",
    },
    "cockpit_entries": {
        None: "Xin台入口配置（可直达的业务系统入口，配置表）",
        "id": "自增主键",
        "key": "入口标识（唯一）",
        "name": "入口名称",
        "entry_path": "XuanPu 站内路径（如 /pro/）",
        "icon": "图标标识",
        "sort": "排序值（升序）",
        "enabled": "是否启用",
        "created_at": "创建时间",
    },
    "cockpit_groups": {
        None: "Xin台卡片分组配置（如 AI工作台 / AI技能 / 业务系统，配置表）",
        "id": "自增主键",
        "key": "分组标识（唯一，如 workbench）",
        "name": "分组名称（如 AI工作台）",
        "sort": "排序值（升序）",
        "enabled": "是否启用",
        "created_at": "创建时间",
    },
    "cockpit_cards": {
        None: "Xin台卡片配置（跳转/技能/会议三类卡片，配置表）",
        "id": "自增主键",
        "key": "卡片标识（唯一，如 minutes）",
        "group_key": "所属分组标识（cockpit_groups.key）",
        "name": "卡片名称（如 实时纪要）",
        "kind": "卡片类型：link=页面跳转 / task=技能任务 / meeting=实时会议",
        "link_url": "跳转地址（/ 开头为 XuanPu 站内免登路径，否则外链）",
        "skill_id": "绑定的 XuanPu 技能 ID（可空，按名称回退匹配）",
        "skill_name": "绑定的技能名称（ID 未配置时回退匹配用）",
        "icon": "图标标识",
        "sort": "排序值（升序）",
        "enabled": "是否启用",
        "created_at": "创建时间",
    },
    "task_records": {
        None: "历史任务（Xin语对话记录与 Xin台执行记录的统一树，含用户文件夹，逻辑删除）",
        "id": "记录 ID（UUID）",
        "user_id": "所属用户 ID",
        "kind": "记录类型：chat=对话 / exec=执行记录 / folder=文件夹",
        "scope": "所属列表：chat=Xin语对话 / exec=Xin台执行",
        "title": "名称（对话标题 / 执行标题 / 文件夹名）",
        "status": "执行状态：running=运行中 / success=成功 / failed=失败 / stopped=已停止",
        "ref_id": "引用 ID（chat=会话 ID，exec=来源卡片标识）",
        "detail": "扩展信息（JSON，如执行输出摘要、会议时长）",
        "parent_id": "上级文件夹 ID（顶层为 NULL）",
        "sort": "同层排序值（降序展示，大者在前）",
    },
    "hero_cards": {
        None: "Xin语页用户自定义卡片（链接/技能，按用户维度，逻辑删除）",
        "id": "卡片 ID（UUID）",
        "user_id": "所属用户 ID",
        "name": "卡片名称",
        "kind": "卡片类型：link=链接 / skill=技能",
        "link_url": "跳转地址（link 卡）",
        "skill_id": "绑定的技能 ID（skill 卡）",
        "skill_name": "绑定的技能名称（skill 卡）",
        "sort": "排序值（升序）",
    },
    "sys_users": {
        None: "系统用户表（本地账号 + 统一身份登录账号）",
        "user_id": "用户 ID（UUID）",
        "username": "登录账号（唯一）",
        "password_hash": "口令哈希（bcrypt；统一身份登录建号为空）",
        "display_name": "姓名",
        "role": "角色：hq_finance=总部财务（含管理员）/ investee_finance=被投企业财务",
        "company": "所属公司",
        "auth_source": "账号来源：local=本地口令 / sso=统一身份登录（自动建号，无本地口令）",
        "xuanpu_user_id": "绑定的 XuanPu 用户 ID（站内系统免登跳转用）",
    },
    "sys_auth_tokens": {
        None: "登录令牌表（本地会话 token 与 XuanPu 网关令牌双轨）",
        "token": "访问令牌",
        "user_id": "所属用户 ID",
        "expires_at": "过期时间",
        "xuanpu_token": "XuanPu 网关令牌（未过期时走 Bearer 鉴权，否则回落 X-User-Name）",
        "xuanpu_token_exp": "XuanPu 网关令牌过期时间",
    },
    "biz_tasks": {
        None: "业务任务表（风险填报 / 资金保障 / KPI 填报等任务的总发起单）",
        "task_id": "任务 ID（UUID）",
        "scene": "任务场景：risk_fill=风险填报 / cash_guarantee=资金保障 / kpi_fill=KPI 填报 / ms_feedback=里程碑反馈 / lamp_adjust=灯色调整 / report=报告 / generic=通用",
        "title": "任务标题",
        "dispatcher_id": "发起人用户 ID",
        "payload": "任务参数（JSON，按场景异构）",
        "period": "所属期间（如 2026-08）",
        "status": "状态：open=进行中 / closed=已关闭",
    },
    "biz_todos": {
        None: "待办表（任务分派到人的待办 / 审核 / 反馈事项）",
        "todo_id": "待办 ID（UUID）",
        "task_id": "所属任务 ID（biz_tasks.task_id）",
        "assignee_id": "负责人用户 ID",
        "kind": "类型：action=待办 / na_confirm=不适用确认 / feedback_review=反馈复核 / review=审核",
        "scene": "场景（同 biz_tasks.scene）",
        "title": "待办标题",
        "sub": "副标题/说明",
        "status": "状态（如 pending=待处理）",
        "lamp": "灯色：r=红 / y=黄 / g=绿",
        "ref": "引用信息（JSON，如关联指标、公司）",
        "feedback_text": "反馈内容",
        "na_reason": "不适用原因",
        "na_comment": "不适用备注",
        "due": "截止时间",
    },
    "risk_fill_batches": {
        None: "风险填报批次表（一次风险信息收集的发起批次）",
        "batch_id": "批次 ID（UUID）",
        "period": "所属期间（如 2026-08）",
        "dispatcher_id": "发起人用户 ID",
        "status": "状态：collecting=收集中 / done=已完成",
    },
    "risk_fill_reports": {
        None: "风险填报报告表（批次内按公司一行的填报状态与灯色汇总）",
        "report_id": "报告 ID（UUID）",
        "batch_id": "所属批次 ID（risk_fill_batches.batch_id）",
        "company": "公司名称",
        "status": "状态：unfilled=未填 / filled=已填 / reviewed=已复核",
        "lamp_r": "红灯条目数",
        "lamp_y": "黄灯条目数",
        "lamp_g": "绿灯条目数",
    },
    "risk_fill_items": {
        None: "风险填报条目表（报告内逐条风险项的灯色与字段值）",
        "id": "自增主键",
        "report_id": "所属报告 ID（risk_fill_reports.report_id）",
        "idx": "条目序号（1-16）",
        "name": "条目名称",
        "lamp": "灯色：r=红 / y=黄 / g=绿",
        "fields": "条目字段值（JSON 数组 [{k,v,pf}]）",
    },
    "cash_guarantee_reports": {
        None: "资金保障报告表（按公司+期间的资金保障能力填报）",
        "form_id": "报告 ID（UUID）",
        "company": "公司名称",
        "period": "所属期间（如 2026-08）",
        "avail_cash": "可用资金（万元）",
        "pooled_fund": "归集资金（万元）",
        "avail_credit": "可用授信（万元）",
        "monthly_outflow": "月度资金流出（万元）",
        "ratio": "资金保障倍数",
        "lamp": "灯色：r=红 / y=黄 / g=绿",
        "status": "状态：draft=草稿 / submitted=已提交 / reviewed=已复核",
        "session_id": "关联会话 ID（AI 填报来源）",
    },
    "kpi_batches": {
        None: "KPI 填报批次表（一次 KPI 信息收集的发起批次）",
        "batch_id": "批次 ID（UUID）",
        "period": "所属期间（如 2026-08）",
        "dispatcher_id": "发起人用户 ID",
        "status": "状态：collecting=收集中 / done=已完成",
    },
    "kpi_indicators": {
        None: "KPI 指标表（批次内按公司+维度的指标填报）",
        "indicator_id": "指标 ID（UUID）",
        "batch_id": "所属批次 ID（kpi_batches.batch_id）",
        "company": "公司名称",
        "dim": "指标维度",
        "name": "指标名称",
        "kpi_type": "指标类型（定量 / 定性）",
        "content": "指标内容/口径",
        "base_score": "基准分",
        "max_score": "封顶分",
        "status": "状态：unfilled=未填 / filled=已填 / reviewed=已复核",
    },
    "kpi_milestones": {
        None: "KPI 里程碑表（指标下的阶段目标与完成情况）",
        "milestone_id": "里程碑 ID（UUID）",
        "indicator_id": "所属指标 ID（kpi_indicators.indicator_id）",
        "content": "里程碑内容",
        "plan_date": "计划完成时间",
        "material": "佐证材料",
        "status": "状态（如 pending=未完成）",
    },
    "kpi_ms_feedbacks": {
        None: "里程碑进展反馈表（被投企业按里程碑提交的进展）",
        "feedback_id": "反馈 ID（UUID）",
        "milestone_id": "所属里程碑 ID（kpi_milestones.milestone_id）",
        "batch_id": "所属批次 ID（kpi_batches.batch_id）",
        "company": "公司名称",
        "status": "进展状态（如 进行中 / 已完成）",
        "progress": "进展百分比（0-100）",
        "actual_date": "实际完成时间",
        "lamp": "灯色：r=红 / y=黄 / g=绿",
        "status_note": "状态说明",
        "review_status": "复核状态：draft=草稿 / submitted=已提交 / reviewed=已复核",
    },
    "kpi_lamp_adjustments": {
        None: "KPI 灯色调整记录表（人工调灯留痕）",
        "id": "自增主键",
        "company": "公司名称",
        "indicator_name": "指标名称",
        "old_lamp": "调整前灯色",
        "new_lamp": "调整后灯色",
        "reason": "调整原因",
        "operator": "操作人",
    },
    "pit_reports": {
        None: "投后报告表（AI 生成的投后管理报告）",
        "report_id": "报告 ID（UUID）",
        "company_ids": "覆盖公司 ID 列表（JSON 数组）",
        "period": "所属期间（如 2026-08）",
        "outline": "报告大纲（JSON 数组）",
        "content": "报告正文",
        "status": "状态：outlining=生成大纲 / draft=撰写中 / done=已完成",
    },
    "kb_sources": {
        None: "知识库来源表（可检索的内部/外部知识库）",
        "kb_id": "知识库 ID",
        "name": "知识库名称",
        "parent_id": "上级知识库 ID（顶层为 NULL）",
        "kb_type": "类型：internal=内部 / external=外部",
        "mcp_ref": "MCP 引用标识",
    },
}


# 可选表：存量库才存在的表（历史遗留废弃表 / langgraph 运行时自建表），
# 表不存在时跳过（全新库不建出这些表，迁移不能因此失败）
_OPTIONAL: dict[str, dict[str | None, str]] = {
    "skill_templates": {
        None: "技能模板表（历史遗留，已废弃：技能体系移除后无代码引用，仅存量库保留）",
        "template_id": "模板 ID（UUID）",
        "skill_key": "技能标识",
        "category": "技能分类",
        "name": "技能名称",
        "sort_no": "排序值",
        "content": "模板内容（JSON）",
        "enabled": "是否启用",
    },
    "user_skills": {
        None: "用户技能表（历史遗留，已废弃：技能体系移除后无代码引用，仅存量库保留）",
        "id": "自增主键",
        "user_id": "所属用户 ID",
        "skill_key": "技能标识",
        "enabled": "是否启用",
    },
    "checkpoints": {
        None: "AI 工作流检查点表（langgraph 运行时自建，保存会话状态快照）",
        "thread_id": "会话线程 ID",
        "checkpoint_ns": "检查点命名空间",
        "checkpoint_id": "检查点 ID",
        "parent_checkpoint_id": "上级检查点 ID",
        "type": "记录类型",
        "checkpoint": "检查点状态（JSON）",
        "metadata": "检查点元数据（JSON）",
    },
    "checkpoint_blobs": {
        None: "AI 工作流通道数据表（langgraph 运行时自建，按通道版本存储状态数据块）",
        "thread_id": "会话线程 ID",
        "checkpoint_ns": "检查点命名空间",
        "channel": "通道名",
        "version": "通道版本",
        "type": "数据类型",
        "blob": "数据块（二进制）",
    },
    "checkpoint_writes": {
        None: "AI 工作流中间写入表（langgraph 运行时自建，保存检查点间的待写入数据）",
        "thread_id": "会话线程 ID",
        "checkpoint_ns": "检查点命名空间",
        "checkpoint_id": "检查点 ID",
        "task_id": "任务 ID",
        "idx": "写入序号",
        "channel": "通道名",
        "type": "数据类型",
        "blob": "数据块（二进制）",
        "task_path": "任务路径",
    },
    "checkpoint_migrations": {
        None: "AI 工作流检查点表结构版本（langgraph 运行时自建）",
        "v": "表结构版本号",
    },
}


def _q(text: str) -> str:
    """SQL 字符串字面量（单引号转义）。"""
    return "'" + text.replace("'", "''") + "'"


def _run(set_comment: bool) -> None:
    # 可选表：存在才写注释（全新库无这些表）
    bind = op.get_bind()
    optional = {
        t: cols for t, cols in _OPTIONAL.items()
        if bind.dialect.has_table(bind, t)
    }
    for table, cols in {**_TABLES, **optional}.items():
        table_comment = cols.get(None)
        value = _q(table_comment) if set_comment and table_comment else "NULL"
        op.execute(f"COMMENT ON TABLE {table} IS {value}")
        # 业务表叠加公共五列注释
        columns: dict[str, str] = dict(_COMMON) if table in _BUSINESS else {}
        columns.update({k: v for k, v in cols.items() if k is not None})
        for col, text in columns.items():
            value = _q(text) if set_comment else "NULL"
            op.execute(f"COMMENT ON COLUMN {table}.{col} IS {value}")


def upgrade() -> None:
    _run(True)


def downgrade() -> None:
    _run(False)
