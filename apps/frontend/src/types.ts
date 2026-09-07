// 契约类型（docs/contract/api.md 唯一事实源）；事件 payload 类型直接从 @contracts/events 引入

export interface UserInfo {
  user_id: string
  username: string
  display_name: string
  role: 'hq_finance' | 'investee_finance'
  company: string | null
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
}

export type TodoStatus =
  | 'pending'
  | 'feedback_submitted'
  | 'na_pending'
  | 'submitted'
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

export interface TodoItem {
  todo_id: string
  task_id: string
  kind: 'action' | 'na_confirm' | 'feedback_review' | 'review'
  scene: TodoScene
  title: string
  sub: string
  status: TodoStatus
  lamp: 'r' | 'y' | 'g' | null
  ref: Record<string, unknown>
  dispatcher_name: string
  due: string | null
  created_at: string
  updated_at: string
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

// ---- XuanPu 平台（经 MCP 网关 /xuanpu REST 代理） ----

export interface XuanPuWorkTask {
  id: number
  name: string
  owner: string
  status: string
  priority: string
  week_start: string
  week_end: string
  is_temporary: boolean
  remark: string
}

export interface XuanPuFillAssignment {
  id: number
  template_id: number
  title: string
  description?: string
  status: string
  note: string
  submitted_at: string
}

/** GET /xuanpu/todos；raw 存在表示 MCP 工具返回的错误文本 */
export interface XuanPuTodos {
  work_tasks: XuanPuWorkTask[]
  fill_assignments: XuanPuFillAssignment[]
  raw?: string
}

/** 填报模板字段定义（模板 schema.fields 项） */
export interface XuanPuFillField {
  key: string
  label: string
  type?: string // 缺省 text；number / multi（多选）
  options?: string[]
  required?: boolean
  placeholder?: string
  help?: string
  section?: string
}

/** GET /xuanpu/fill/template；raw 存在表示 MCP 工具返回的错误文本 */
export interface XuanPuFillTemplateDetail {
  template_id?: number
  title?: string
  description?: string | null
  status?: string
  fields: XuanPuFillField[] | null
  raw?: string
}

/** GET /xuanpu/fill/assignment（含已存草稿）；raw 存在表示 MCP 工具返回的错误文本 */
export interface XuanPuFillAssignmentDetail {
  assignment_id: number
  template_id: number
  title: string
  description: string | null
  status: string
  note: string
  submitted_at: string
  draft: Record<string, unknown> | null
  raw?: string
}

export interface XuanPuStatGroup {
  groups: { key: string; count: number }[]
  total: number
}

export interface XuanPuProgressTask {
  id: number
  name: string
  owner: string
  status: string
  progress: number
  start_date: string
  end_date: string
}

/** GET /xuanpu/dashboard；raw 存在表示 MCP 工具返回的错误文本 */
export interface XuanPuDashboard {
  active_project: { id?: number; name: string; status?: string } | null
  projects: { id: number; name: string; status: string }[]
  progress_tasks: XuanPuProgressTask[]
  progress_tasks_total: number
  bug_stats_by_priority?: XuanPuStatGroup
  bug_stats_by_fix_status?: XuanPuStatGroup
  req_stats_by_status?: XuanPuStatGroup
  raw?: string
}

/** GET /xuanpu/skills 项（xuanpu_skills 网关工具） */
export interface XuanPuSkill {
  id: number
  name: string
  category?: string | null
  trigger_type?: string | null
  description?: string | null
  is_active?: boolean
}

/** POST /xuanpu/skills/{id}/run 结果（异步运行时轮询至终态）；raw 为网关错误文本 */
export interface XuanPuSkillRunResult {
  execution_id?: number | string
  status?: string // success | failed | timeout
  output_data?: unknown
  error?: string
  duration_ms?: number
  note?: string
  raw?: string
}

/** GET /cockpit/entries 项（Xin台系统入口，launch 跳转目标） */
export interface CockpitEntry {
  id: number
  key: string
  name: string
  entry_path: string
  icon?: string | null
  sort: number
  enabled: boolean
}
