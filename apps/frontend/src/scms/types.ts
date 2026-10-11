// 契约类型（docs/contract/api.md 唯一事实源）；事件 payload 类型直接从 @contracts/events 引入

export interface UserInfo {
  user_id: string
  username: string
  display_name: string
  role: 'hq_finance' | 'company_operator' | 'investee_finance' | 'member'
  company: string | null
  role_names?: string[]
}

export interface SessionHeader {
  session_id: string
  user_id: string
  title: string | null
  domain: string
  status: 'active' | 'archived'
  created_at: string
  updated_at: string
}

export interface SessionListItem extends SessionHeader {
  last_message: string | null
  pending_interaction: boolean
  task_type?: string // 会话类型：只按首轮工具判定（qa/post_report/fin_risk_report/todo）；历史分组用
}

// ===== AI 问数技能（GET /skills 目录 + 模版）=====
export interface SkillItem {
  skill_key: string
  name: string
  sort_no: number
  core: boolean
  desc: string
  file_type?: string
  enabled: boolean
}

export interface SkillTemplate {
  template_id: string
  skill_key: string
  category: string
  name: string
  sort_no: number
  enabled: boolean
  content: {
    tool?: string // 工具调用地址（点击后触发的话术/工具）
    prompt?: string // 触发话术（点击模版 → 作为问数内容发送）
    file_type?: string
    status?: string // 'dev' = 开发中
    [k: string]: unknown
  }
}

export type TodoStatus =
  | 'pending'
  | 'feedback_submitted'
  | 'na_pending'
  | 'submitted'
  | 'returned'
  | 'completed'
  | 'na_closed'

export type TodoScene =
  | 'risk_fill'
  | 'cash_guarantee'
  | 'kpi_fill'
  | 'ms_feedback'
  | 'lamp_adjust'
  | 'report'
  | 'generic'
  | 'oa'
  | 'dynamic_fill'

export interface TodoItem {
  todo_id: string
  task_id: string
  assignee_id: string
  assignee_name: string
  kind: 'action' | 'na_confirm' | 'feedback_review' | 'review'
  scene: TodoScene
  title: string
  sub: string
  status: TodoStatus
  lamp: 'r' | 'y' | 'g' | null
  lamp_agg: Record<string, number>
  ref: Record<string, unknown>
  payload: Record<string, unknown> | null
  completion_text: string | null
  submitted_at: string | null
  returned_at: string | null
  completed_at: string | null
  return_reason: string | null
  remind_count: number
  last_reminded_at: string | null
  review_required: boolean
  period_locked: boolean
  overdue: boolean
  session_id: string | null
  dispatcher_name: string
  due: string | null
  created_at: string
  updated_at: string
  // 任务模板（小流程）相关：form 为派发时的模板表单快照
  template_key: string | null
  // 动态填报模板任务（dyn_ 前缀模板派发）：非空 → 前端走 FillModal（iframe HTML 表单）
  dynamic_template_id: string | null
  related_system: string | null
  anchor: Record<string, unknown>
  form: FormSchema | null
}

/** 任务模板表单 schema（后端模板 JSON 声明，随任务快照下发） */
export interface FormColumn {
  k: string
  label: string
}

export interface FormField {
  k: string
  label: string
  type: 'text' | 'textarea' | 'number' | 'date' | 'select' | 'table'
  required?: boolean
  placeholder?: string
  options?: string[]
  columns?: FormColumn[]
}

export interface FormSchema {
  fields: FormField[]
}

export interface TodoDetail extends TodoItem {
  payload: Record<string, unknown>
}

// ===== 今日工作要点（GET /todos/brief；brief/ready 推送同构）=====
export type BriefKind = 'overdue' | 'due_today' | 'returned' | 'to_review' | 'to_na_confirm' | 'silent'

export interface BriefItem {
  todo_id: string
  task_id: string
  title: string
  status: TodoStatus
  kind: BriefKind
  due: string | null
  assignee_name: string
}

export interface DailyBrief {
  date: string
  counts: Record<BriefKind, number>
  items: BriefItem[]
}

// ===== 组织架构树（GET /org/tree，树选模式）=====
export interface OrgTreeNode {
  id: string
  label: string
  /** 子树总人数（含各级子孙部门） */
  count: number
  users: DirectoryEntry[]
  children: OrgTreeNode[]
}

export interface OrgTreeResp {
  tree: OrgTreeNode[]
  /** 搜索时跨部门命中的用户平铺（树过滤的兜底列表） */
  matched_users: DirectoryEntry[]
}

// ===== 任务模板目录（GET /tasks/templates）=====
export interface TemplateView {
  template_key: string
  name: string
  scene: string
  review_required: boolean
  related_system: string | null
  period_rule: string
  creator_roles: string[]
}

// ===== 调灯流水 / 备注 =====
export interface LampLogItem {
  field_path: string
  old_lamp: string | null
  new_lamp: string | null
  reason: string
  operator_id: string
  at: string
}

export interface RemarkItem {
  remark_id: string
  content: string
  author_id: string
  at: string
}

// ===== 任务交付物（附件，/artifacts）=====
export interface ArtifactItem {
  artifact_id: string
  todo_id: string
  task_id: string | null
  file_name: string
  mime_type: string | null
  file_size: number | null
  kb_included: boolean
  uploaded_by: string | null
  created_at: string
}

// ===== 组织目录（@ 选择器 / 派发范围）=====
export interface DirectoryEntry {
  id: string
  label: string
  extra?: string
}

export interface ExpandResult {
  user_ids: string[]
  labels: string[]
  unknown: unknown[]
}

// ===== 任务看板（/tasks）=====
export interface TaskView {
  task_id: string
  parent_task_id: string | null
  template_key: string | null
  scene: string
  title: string
  period: string | null
  status: 'open' | 'ended'
  related_system: string | null
  review_required: boolean
  due: string | null
  end_policy: string
  ended_at: string | null
  spawn_meta: Record<string, unknown>
  dispatcher_name: string
  created_at: string
  // 动态填报模板 ID（非空 → 动态模板任务，看板/分析走 analytics 数据源）
  dynamic_template_id: string | null
}

export interface BoardTodo {
  todo_id: string
  assignee_id: string
  assignee_name: string
  title: string
  status: TodoStatus
  remind_count: number
  last_reminded_at: string | null
  submitted_at: string | null
  returned_at: string | null
  completed_at: string | null
  completion_text: string | null
  return_reason: string | null
  due: string | null
  created_at?: string // 下发时间（临时看板明细列）
}

export interface TaskBoard extends TaskView {
  stats: Record<string, number>
  groups: Record<string, BoardTodo[]>
}

export interface DashboardSummary {
  overview: { open_tasks: number; completed_7d: number; completion_rate: number; overdue: number }
  by_scene: { scene: string; total: number; done: number }[]
  todo_funnel: { status: TodoStatus; count: number }[]
  risk_board: {
    batch_id: string
    period: string
    companies: { company: string; status: 'unfilled' | 'filled' | 'reviewed' }[]
    lamps: { r: number; y: number; g: number }
  } | null
  trend_14d: { date: string; created: number; completed: number }[]
}

export interface RiskBatch {
  batch_id: string
  period: string
  dispatcher_id: string
  status: 'collecting' | 'done'
  created_at: string
}

export interface RiskField {
  k: string
  v: string
  pf?: boolean
}

export interface RiskItem {
  idx: number
  name: string
  lamp: 'r' | 'y' | 'g'
  fields: RiskField[]
}

export interface RiskReport {
  report_id: string
  batch_id: string
  company: string
  status: 'unfilled' | 'filled' | 'reviewed'
  lamp_r: number
  lamp_y: number
  lamp_g: number
  items: RiskItem[]
}

export interface CashReport {
  form_id: string
  company: string
  period: string
  avail_cash: number
  pooled_fund: number
  avail_credit: number
  monthly_outflow: number
  ratio: number
  lamp: 'r' | 'y' | 'g'
  status: 'draft' | 'submitted' | 'reviewed'
}

export interface KpiIndicator {
  indicator_id: string
  dim: string
  name: string
  kpi_type: string
  content: string
  base_score: string
  max_score: string
  status?: string
}

export interface KpiMilestone {
  milestone_id?: string
  indicator_id: string
  content: string
  plan_date: string
  material: string
  status?: string
}

export interface MsFeedback {
  feedback_id: string
  company: string
  milestone_content: string
  status: string
  progress: number
  actual_date?: string
  status_note?: string
  lamp: 'r' | 'y' | 'g'
  review_status?: string
}

export interface KbSource {
  kb_id: string
  name: string
  parent_id: string | null
  kb_type: 'internal' | 'external'
}

export interface PitReport {
  report_id: string
  company_ids: string[]
  period: string
  outline: string[]
  content: string
  status: 'outlining' | 'draft' | 'done'
}

/** 平台事件（帧 data 解包后形态：seq/time 提升，其余入 data） */
export interface PlatformEvent {
  seq: number
  type: string
  time: string
  data: Record<string, any>
  ignorable?: boolean
}

// ===== OA 插件（plugins/oa-workflow；移除插件时同步删除本段）=====

export interface OaRoleInfo {
  role_id: number
  role_name: string
  role_key: string
}

export interface OaMenuNode {
  menu_id: number
  menu_name: string
  parent_id: number
  order_num: number
  path: string
  component: string | null
  menu_type: 'M' | 'C' | 'F'
  perms: string
  visible: string
  children: OaMenuNode[]
}

export interface OaMe {
  user: {
    user_id: string
    user_name: string
    nick_name: string
    dept_id: number
    dept_name: string
    phonenumber: string
    email: string
  } | null
  is_admin: boolean
  roles: OaRoleInfo[]
  perms: string[]
  menus: OaMenuNode[]
  data_scope: string
}

export interface OaTemplate extends TemplateView {
  approver_role: string
  form: FormSchema
}

export interface OaTodoItem extends TodoItem {
  assigner_name?: string
}

export interface OaApprovalItem extends TodoItem {
  applicant_name?: string
}

export interface OaDirectoryUser {
  user_id: string
  user_name: string
  nick_name: string
  dept_id: number
  dept_name: string
}
