-- ============================================================================
-- XinHere 数据库初始化脚本（三）：表与字段 COMMENT
-- 用法：psql -U dbuser -d xinhere -f scripts/db/03_comments.sql
-- 幂等可重复执行。
-- ============================================================================

-- ---------------- 平台表 ----------------
COMMENT ON TABLE platform_sessions IS '平台会话头表：一次对话一个会话，记录用户/领域/插件集（append-only，无 delete）';
COMMENT ON COLUMN platform_sessions.session_id IS '会话ID（UUID，应用侧生成）';
COMMENT ON COLUMN platform_sessions.user_id IS '所属用户ID（sys_users.user_id）';
COMMENT ON COLUMN platform_sessions.title IS '会话标题（可由首条消息派生）';
COMMENT ON COLUMN platform_sessions.domain IS '业务领域标识（决定插件集）';
COMMENT ON COLUMN platform_sessions.plugin_set IS '本会话装配的插件集（JSONB）';
COMMENT ON COLUMN platform_sessions.plugin_set_hash IS '插件集指纹哈希（变更检测）';
COMMENT ON COLUMN platform_sessions.status IS '会话状态：active 活跃 / archived 归档';
COMMENT ON COLUMN platform_sessions.created_at IS '创建时间';
COMMENT ON COLUMN platform_sessions.updated_at IS '更新时间（触发器 trg_platform_sessions_updated_at 维护）';

COMMENT ON TABLE platform_session_events IS '平台会话事件表：append-only 事件日志，(session_id, seq) 会话内 0 起连续，事件族驱动前端组件渲染';
COMMENT ON COLUMN platform_session_events.session_id IS '所属会话ID（外键 platform_sessions.session_id）';
COMMENT ON COLUMN platform_session_events.seq IS '会话内序号，0 起连续（复合主键之一）';
COMMENT ON COLUMN platform_session_events.type IS '事件类型（事件词表，见 packages/contracts/schemas/core.json）';
COMMENT ON COLUMN platform_session_events.time IS '事件发生时间';
COMMENT ON COLUMN platform_session_events.data IS '事件载荷（JSONB，结构随 type 而定）';
COMMENT ON COLUMN platform_session_events.ignorable IS '是否可忽略事件（UI 可跳过渲染）';
COMMENT ON COLUMN platform_session_events.turn IS '所属对话轮次（可空）';

COMMENT ON TABLE platform_compensations IS '平台补偿任务表：副作用补偿队列（pending/done/failed）';
COMMENT ON COLUMN platform_compensations.id IS '自增主键';
COMMENT ON COLUMN platform_compensations.session_id IS '触发补偿的会话ID';
COMMENT ON COLUMN platform_compensations.event_seq IS '关联事件序号';
COMMENT ON COLUMN platform_compensations.plugin_name IS '补偿所属插件名';
COMMENT ON COLUMN platform_compensations.action IS '补偿动作标识';
COMMENT ON COLUMN platform_compensations.payload IS '补偿载荷（JSONB）';
COMMENT ON COLUMN platform_compensations.status IS '补偿状态：pending 待执行 / done 完成 / failed 失败';
COMMENT ON COLUMN platform_compensations.retry_count IS '已重试次数';
COMMENT ON COLUMN platform_compensations.created_at IS '创建时间';

COMMENT ON TABLE platform_operation_logs IS '系统级操作日志：insert/update/select 全量留痕（append-only，无 update/delete）';
COMMENT ON COLUMN platform_operation_logs.id IS '自增主键';
COMMENT ON COLUMN platform_operation_logs.occurred_at IS '操作发生时间';
COMMENT ON COLUMN platform_operation_logs.user_id IS '操作者用户ID（匿名时为 anonymous）';
COMMENT ON COLUMN platform_operation_logs.session_id IS '关联会话ID（可空）';
COMMENT ON COLUMN platform_operation_logs.channel IS '操作渠道（page 页面 / system 系统 / agent 智能体）';
COMMENT ON COLUMN platform_operation_logs.actor IS '操作者展示名（如 page:/api/v1/... 或用户姓名）';
COMMENT ON COLUMN platform_operation_logs.plugin_name IS '所属插件名（可空）';
COMMENT ON COLUMN platform_operation_logs.entity IS '操作实体（表名，多个逗号分隔）';
COMMENT ON COLUMN platform_operation_logs.operation IS '操作类型：select / insert / update';
COMMENT ON COLUMN platform_operation_logs.record_key IS '记录主键（复合主键用 | 分隔，可空）';
COMMENT ON COLUMN platform_operation_logs.detail IS '操作详情（JSONB：criteria/rows/new/changes，敏感字段已脱敏）';
COMMENT ON COLUMN platform_operation_logs.client_ip IS '客户端 IP';
COMMENT ON COLUMN platform_operation_logs.entry_point IS '入口（HTTP 方法 + 路径 / python -m ...）';
COMMENT ON COLUMN platform_operation_logs.request_id IS '请求追踪ID（X-Request-Id）';

COMMENT ON TABLE platform_projections IS '平台会话投影表：事件流派生状态的物化快照（每会话每 key 一行）';
COMMENT ON COLUMN platform_projections.session_id IS '会话ID（复合主键之一）';
COMMENT ON COLUMN platform_projections.key IS '投影键（如 todo 看板状态，复合主键之一）';
COMMENT ON COLUMN platform_projections.state_version IS '投影状态版本号（默认 1）';
COMMENT ON COLUMN platform_projections.seq IS '派生所至的事件 seq';
COMMENT ON COLUMN platform_projections.value IS '投影状态值（JSONB）';

-- ---------------- 用户与认证 ----------------
COMMENT ON TABLE sys_users IS '用户表：总部财务 / 被投企业财务 / 管理员';
COMMENT ON COLUMN sys_users.user_id IS '用户ID（UUID 字符串，应用侧生成）';
COMMENT ON COLUMN sys_users.username IS '登录名（唯一）';
COMMENT ON COLUMN sys_users.password_hash IS 'bcrypt 密码哈希';
COMMENT ON COLUMN sys_users.display_name IS '展示姓名';
COMMENT ON COLUMN sys_users.role IS '角色：hq_finance 总部财务（含 admin 权限）/ investee_finance 被投企业财务';
COMMENT ON COLUMN sys_users.company IS '所属被投企业（总部用户为空）';
COMMENT ON COLUMN sys_users.is_delete IS '逻辑删除标记（红线1：物理不删，查询默认过滤）';
COMMENT ON COLUMN sys_users.created_by IS '创建人（session before_flush 自动填充）';
COMMENT ON COLUMN sys_users.created_at IS '创建时间';
COMMENT ON COLUMN sys_users.updated_by IS '最后修改人（session before_flush 自动填充）';
COMMENT ON COLUMN sys_users.updated_at IS '更新时间（触发器维护）';

COMMENT ON TABLE sys_auth_tokens IS '认证令牌表：登录签发的 Bearer Token';
COMMENT ON COLUMN sys_auth_tokens.token IS '令牌（主键）';
COMMENT ON COLUMN sys_auth_tokens.user_id IS '所属用户ID';
COMMENT ON COLUMN sys_auth_tokens.expires_at IS '过期时间（默认 72 小时）';
COMMENT ON COLUMN sys_auth_tokens.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN sys_auth_tokens.created_by IS '创建人';
COMMENT ON COLUMN sys_auth_tokens.created_at IS '创建时间';
COMMENT ON COLUMN sys_auth_tokens.updated_by IS '最后修改人';
COMMENT ON COLUMN sys_auth_tokens.updated_at IS '更新时间（触发器维护）';

-- ---------------- 任务与待办 ----------------
COMMENT ON TABLE biz_tasks IS '业务任务表：总部向被投企业派发的任务头表';
COMMENT ON COLUMN biz_tasks.task_id IS '任务ID（UUID 字符串，应用侧生成）';
COMMENT ON COLUMN biz_tasks.scene IS '任务场景：risk_fill 风险填报 / cash_guarantee 资金保障 / kpi_fill KPI填报 / ms_feedback 里程碑反馈 / lamp_adjust 灯调整 / report 投后报告 / generic 通用';
COMMENT ON COLUMN biz_tasks.title IS '任务标题';
COMMENT ON COLUMN biz_tasks.dispatcher_id IS '派发人用户ID';
COMMENT ON COLUMN biz_tasks.payload IS '任务载荷（JSONB，随场景而定）';
COMMENT ON COLUMN biz_tasks.period IS '业务期间（如 2026-08）';
COMMENT ON COLUMN biz_tasks.status IS '任务状态：open 进行中 / closed 已关闭';
COMMENT ON COLUMN biz_tasks.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN biz_tasks.created_by IS '创建人';
COMMENT ON COLUMN biz_tasks.created_at IS '创建时间';
COMMENT ON COLUMN biz_tasks.updated_by IS '最后修改人';
COMMENT ON COLUMN biz_tasks.updated_at IS '更新时间（触发器维护）';

COMMENT ON TABLE biz_todos IS '待办表：任务落到个人的待办项（工作台驱动）';
COMMENT ON COLUMN biz_todos.todo_id IS '待办ID（UUID 字符串，应用侧生成）';
COMMENT ON COLUMN biz_todos.task_id IS '所属任务ID（biz_tasks.task_id）';
COMMENT ON COLUMN biz_todos.assignee_id IS '办理人用户ID';
COMMENT ON COLUMN biz_todos.kind IS '待办类型：action 办理 / na_confirm 不适用确认 / feedback_review 反馈审核 / review 审核';
COMMENT ON COLUMN biz_todos.scene IS '业务场景（同 biz_tasks.scene，默认 generic）';
COMMENT ON COLUMN biz_todos.title IS '待办标题';
COMMENT ON COLUMN biz_todos.sub IS '副标题/补充说明';
COMMENT ON COLUMN biz_todos.status IS '状态机：pending 待办 / doing 办理中 / feedback_submitted 反馈已提交 / na_pending 不适用待确认 / done 完成（随场景而异）';
COMMENT ON COLUMN biz_todos.lamp IS '红黄绿灯：r / y / g（可空）';
COMMENT ON COLUMN biz_todos.ref IS '业务引用（JSONB：指向表单/批次等业务对象）';
COMMENT ON COLUMN biz_todos.feedback_text IS '反馈文本（反馈类待办）';
COMMENT ON COLUMN biz_todos.na_reason IS '不适用原因';
COMMENT ON COLUMN biz_todos.na_comment IS '不适用审核意见';
COMMENT ON COLUMN biz_todos.due IS '截止时间';
COMMENT ON COLUMN biz_todos.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN biz_todos.created_by IS '创建人';
COMMENT ON COLUMN biz_todos.created_at IS '创建时间';
COMMENT ON COLUMN biz_todos.updated_by IS '最后修改人';
COMMENT ON COLUMN biz_todos.updated_at IS '更新时间（触发器维护）';

-- ---------------- 风险填报（risk-warning 插件） ----------------
COMMENT ON TABLE risk_fill_batches IS '风险填报批次表：一次风险填报任务一批，总部按期间发起';
COMMENT ON COLUMN risk_fill_batches.batch_id IS '批次ID（UUID 字符串，应用侧生成）';
COMMENT ON COLUMN risk_fill_batches.period IS '填报期间（如 2026-08）';
COMMENT ON COLUMN risk_fill_batches.dispatcher_id IS '发起人用户ID';
COMMENT ON COLUMN risk_fill_batches.status IS '批次状态：collecting 收集中 / done 已完成';
COMMENT ON COLUMN risk_fill_batches.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN risk_fill_batches.created_by IS '创建人';
COMMENT ON COLUMN risk_fill_batches.created_at IS '创建时间';
COMMENT ON COLUMN risk_fill_batches.updated_by IS '最后修改人';
COMMENT ON COLUMN risk_fill_batches.updated_at IS '更新时间（触发器维护）';

COMMENT ON TABLE risk_fill_reports IS '风险填报表单表：每批次每被投企业一份';
COMMENT ON COLUMN risk_fill_reports.report_id IS '表单ID（UUID 字符串，应用侧生成）';
COMMENT ON COLUMN risk_fill_reports.batch_id IS '所属批次ID（risk_fill_batches.batch_id）';
COMMENT ON COLUMN risk_fill_reports.company IS '被投企业名称';
COMMENT ON COLUMN risk_fill_reports.status IS '填报状态：unfilled 未填 / filled 已填 / reviewed 已复核';
COMMENT ON COLUMN risk_fill_reports.lamp_r IS '红灯项数量';
COMMENT ON COLUMN risk_fill_reports.lamp_y IS '黄灯项数量';
COMMENT ON COLUMN risk_fill_reports.lamp_g IS '绿灯项数量';
COMMENT ON COLUMN risk_fill_reports.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN risk_fill_reports.created_by IS '创建人';
COMMENT ON COLUMN risk_fill_reports.created_at IS '创建时间';
COMMENT ON COLUMN risk_fill_reports.updated_by IS '最后修改人';
COMMENT ON COLUMN risk_fill_reports.updated_at IS '更新时间（触发器维护）';

COMMENT ON TABLE risk_fill_items IS '风险填报明细项表：每份报告 16 个风险项';
COMMENT ON COLUMN risk_fill_items.id IS '自增主键';
COMMENT ON COLUMN risk_fill_items.report_id IS '所属表单ID（risk_fill_reports.report_id）';
COMMENT ON COLUMN risk_fill_items.idx IS '项序号（1-16）';
COMMENT ON COLUMN risk_fill_items.name IS '风险项名称';
COMMENT ON COLUMN risk_fill_items.lamp IS '灯色：r 红 / y 黄 / g 绿';
COMMENT ON COLUMN risk_fill_items.fields IS '风险项字段值（JSONB 数组 [{k,v,pf?}]，pf 为上期值）';
COMMENT ON COLUMN risk_fill_items.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN risk_fill_items.created_by IS '创建人';
COMMENT ON COLUMN risk_fill_items.created_at IS '创建时间';
COMMENT ON COLUMN risk_fill_items.updated_by IS '最后修改人';
COMMENT ON COLUMN risk_fill_items.updated_at IS '更新时间（触发器维护）';

-- ---------------- 资金保障（risk-warning 插件） ----------------
COMMENT ON TABLE cash_guarantee_reports IS '资金保障填报表：每企业每期间一份，金额单位万元';
COMMENT ON COLUMN cash_guarantee_reports.form_id IS '表单ID（UUID 字符串，应用侧生成）';
COMMENT ON COLUMN cash_guarantee_reports.company IS '被投企业名称';
COMMENT ON COLUMN cash_guarantee_reports.period IS '填报期间（如 2026-08）';
COMMENT ON COLUMN cash_guarantee_reports.avail_cash IS '可用资金（万元）';
COMMENT ON COLUMN cash_guarantee_reports.pooled_fund IS '归集资金（万元）';
COMMENT ON COLUMN cash_guarantee_reports.avail_credit IS '可用授信（万元）';
COMMENT ON COLUMN cash_guarantee_reports.monthly_outflow IS '月度刚性流出（万元）';
COMMENT ON COLUMN cash_guarantee_reports.ratio IS '资金保障倍数（自动计算）';
COMMENT ON COLUMN cash_guarantee_reports.lamp IS '灯色：r 红 / y 黄 / g 绿';
COMMENT ON COLUMN cash_guarantee_reports.status IS '状态：draft 草稿 / submitted 已提交 / reviewed 已复核';
COMMENT ON COLUMN cash_guarantee_reports.session_id IS '填写该表单的会话ID（可空，用于对话续填）';
COMMENT ON COLUMN cash_guarantee_reports.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN cash_guarantee_reports.created_by IS '创建人';
COMMENT ON COLUMN cash_guarantee_reports.created_at IS '创建时间';
COMMENT ON COLUMN cash_guarantee_reports.updated_by IS '最后修改人';
COMMENT ON COLUMN cash_guarantee_reports.updated_at IS '更新时间（触发器维护）';

-- ---------------- KPI 考核（kpi-assessment 插件） ----------------
COMMENT ON TABLE kpi_batches IS 'KPI 考核批次表：总部按期间发起的考核批次';
COMMENT ON COLUMN kpi_batches.batch_id IS '批次ID（UUID 字符串，应用侧生成）';
COMMENT ON COLUMN kpi_batches.period IS '考核期间（如 2026-H1）';
COMMENT ON COLUMN kpi_batches.dispatcher_id IS '发起人用户ID';
COMMENT ON COLUMN kpi_batches.status IS '批次状态：collecting 收集中 / done 已完成';
COMMENT ON COLUMN kpi_batches.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN kpi_batches.created_by IS '创建人';
COMMENT ON COLUMN kpi_batches.created_at IS '创建时间';
COMMENT ON COLUMN kpi_batches.updated_by IS '最后修改人';
COMMENT ON COLUMN kpi_batches.updated_at IS '更新时间（触发器维护）';

COMMENT ON TABLE kpi_indicators IS 'KPI 指标表：每批次每企业的考核指标';
COMMENT ON COLUMN kpi_indicators.indicator_id IS '指标ID（UUID 字符串，应用侧生成）';
COMMENT ON COLUMN kpi_indicators.batch_id IS '所属批次ID（kpi_batches.batch_id）';
COMMENT ON COLUMN kpi_indicators.company IS '被投企业名称';
COMMENT ON COLUMN kpi_indicators.dim IS '考核维度';
COMMENT ON COLUMN kpi_indicators.name IS '指标名称';
COMMENT ON COLUMN kpi_indicators.kpi_type IS '指标类型：定量 / 定性';
COMMENT ON COLUMN kpi_indicators.content IS '指标内容/口径说明';
COMMENT ON COLUMN kpi_indicators.base_score IS '基准分';
COMMENT ON COLUMN kpi_indicators.max_score IS '最高分';
COMMENT ON COLUMN kpi_indicators.status IS '填报状态：unfilled 未填 / filled 已填 / reviewed 已复核';
COMMENT ON COLUMN kpi_indicators.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN kpi_indicators.created_by IS '创建人';
COMMENT ON COLUMN kpi_indicators.created_at IS '创建时间';
COMMENT ON COLUMN kpi_indicators.updated_by IS '最后修改人';
COMMENT ON COLUMN kpi_indicators.updated_at IS '更新时间（触发器维护）';

COMMENT ON TABLE kpi_milestones IS 'KPI 里程碑表：指标下的里程碑计划';
COMMENT ON COLUMN kpi_milestones.milestone_id IS '里程碑ID（UUID 字符串，应用侧生成）';
COMMENT ON COLUMN kpi_milestones.indicator_id IS '所属指标ID（kpi_indicators.indicator_id）';
COMMENT ON COLUMN kpi_milestones.content IS '里程碑内容';
COMMENT ON COLUMN kpi_milestones.plan_date IS '计划完成日期（字符串，如 2026-09-30）';
COMMENT ON COLUMN kpi_milestones.material IS '佐证材料说明';
COMMENT ON COLUMN kpi_milestones.status IS '状态：pending 未开始 / doing 进行中 / done 已完成';
COMMENT ON COLUMN kpi_milestones.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN kpi_milestones.created_by IS '创建人';
COMMENT ON COLUMN kpi_milestones.created_at IS '创建时间';
COMMENT ON COLUMN kpi_milestones.updated_by IS '最后修改人';
COMMENT ON COLUMN kpi_milestones.updated_at IS '更新时间（触发器维护）';

COMMENT ON TABLE kpi_ms_feedbacks IS 'KPI 里程碑反馈表：被投企业对里程碑进展的填报';
COMMENT ON COLUMN kpi_ms_feedbacks.feedback_id IS '反馈ID（UUID 字符串，应用侧生成）';
COMMENT ON COLUMN kpi_ms_feedbacks.milestone_id IS '里程碑ID（kpi_milestones.milestone_id）';
COMMENT ON COLUMN kpi_ms_feedbacks.batch_id IS '所属批次ID（冗余，便于按批查询）';
COMMENT ON COLUMN kpi_ms_feedbacks.company IS '被投企业名称';
COMMENT ON COLUMN kpi_ms_feedbacks.status IS '进展状态（如 进行中 / 已完成 / 延期）';
COMMENT ON COLUMN kpi_ms_feedbacks.progress IS '进度百分比（0-100）';
COMMENT ON COLUMN kpi_ms_feedbacks.actual_date IS '实际完成日期（可空）';
COMMENT ON COLUMN kpi_ms_feedbacks.lamp IS '灯色：r 红 / y 黄 / g 绿';
COMMENT ON COLUMN kpi_ms_feedbacks.status_note IS '状态说明';
COMMENT ON COLUMN kpi_ms_feedbacks.review_status IS '审核状态：draft 草稿 / submitted 已提交 / reviewed 已复核';
COMMENT ON COLUMN kpi_ms_feedbacks.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN kpi_ms_feedbacks.created_by IS '创建人';
COMMENT ON COLUMN kpi_ms_feedbacks.created_at IS '创建时间';
COMMENT ON COLUMN kpi_ms_feedbacks.updated_by IS '最后修改人';
COMMENT ON COLUMN kpi_ms_feedbacks.updated_at IS '更新时间（触发器维护）';

COMMENT ON TABLE kpi_lamp_adjustments IS 'KPI 灯调整记录表：总部人工调整灯色的留痕';
COMMENT ON COLUMN kpi_lamp_adjustments.id IS '自增主键';
COMMENT ON COLUMN kpi_lamp_adjustments.company IS '被投企业名称';
COMMENT ON COLUMN kpi_lamp_adjustments.indicator_name IS '指标名称';
COMMENT ON COLUMN kpi_lamp_adjustments.old_lamp IS '调整前灯色：r/y/g';
COMMENT ON COLUMN kpi_lamp_adjustments.new_lamp IS '调整后灯色：r/y/g';
COMMENT ON COLUMN kpi_lamp_adjustments.reason IS '调整原因';
COMMENT ON COLUMN kpi_lamp_adjustments.operator IS '操作人';
COMMENT ON COLUMN kpi_lamp_adjustments.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN kpi_lamp_adjustments.created_by IS '创建人';
COMMENT ON COLUMN kpi_lamp_adjustments.created_at IS '创建时间';
COMMENT ON COLUMN kpi_lamp_adjustments.updated_by IS '最后修改人';
COMMENT ON COLUMN kpi_lamp_adjustments.updated_at IS '更新时间（触发器维护）';

-- ---------------- 投后报告（post-investment 插件） ----------------
COMMENT ON TABLE pit_reports IS '投后报告表：AI 生成投后分析报告（先大纲后正文）';
COMMENT ON COLUMN pit_reports.report_id IS '报告ID（UUID 字符串，应用侧生成）';
COMMENT ON COLUMN pit_reports.company_ids IS '覆盖企业列表（JSONB 数组）';
COMMENT ON COLUMN pit_reports.period IS '报告期间（如 2026-08）';
COMMENT ON COLUMN pit_reports.outline IS '报告大纲（JSONB 数组，会话内可编辑确认）';
COMMENT ON COLUMN pit_reports.content IS '报告正文（Markdown）';
COMMENT ON COLUMN pit_reports.status IS '生成状态：outlining 大纲中 / draft 草稿 / done 完成';
COMMENT ON COLUMN pit_reports.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN pit_reports.created_by IS '创建人';
COMMENT ON COLUMN pit_reports.created_at IS '创建时间';
COMMENT ON COLUMN pit_reports.updated_by IS '最后修改人';
COMMENT ON COLUMN pit_reports.updated_at IS '更新时间（触发器维护）';

-- ---------------- 知识库 ----------------
COMMENT ON TABLE kb_sources IS '知识库节点表：树形结构（企业/部门/项目/个人/外部），对接外部知识库 MCP';
COMMENT ON COLUMN kb_sources.kb_id IS '知识库节点ID（固定语义 ID，如 kb-enterprise）';
COMMENT ON COLUMN kb_sources.name IS '知识库名称';
COMMENT ON COLUMN kb_sources.parent_id IS '父节点ID（NULL 为根）';
COMMENT ON COLUMN kb_sources.kb_type IS '类型：internal 内部 / external 外部（经 mcp_ref 指向外部 MCP）';
COMMENT ON COLUMN kb_sources.mcp_ref IS '外部知识库 MCP 引用（kb_type=external 时使用）';
COMMENT ON COLUMN kb_sources.is_delete IS '逻辑删除标记（红线1）';
COMMENT ON COLUMN kb_sources.created_by IS '创建人';
COMMENT ON COLUMN kb_sources.created_at IS '创建时间';
COMMENT ON COLUMN kb_sources.updated_by IS '最后修改人';
COMMENT ON COLUMN kb_sources.updated_at IS '更新时间（触发器维护）';
