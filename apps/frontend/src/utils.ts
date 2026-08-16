// 通用小工具：工具名中文映射、日期分组、格式化
export const TOOL_ZH: Record<string, string> = {
  search_knowledge: '检索知识库',
  list_companies: '获取企业清单',
  dispatch_risk_fill: '发起风险填报',
  get_risk_fill_status: '查询填报进度',
  start_cash_guarantee_fill: '现金保障试算',
  dispatch_kpi_fill: '发起经营者考核',
  dispatch_ms_feedback: '发起里程碑反馈',
  adjust_lamp: '亮灯调整',
  generate_post_report: '生成投后报告',
  dispatch_generic_task: '派发通用任务',
  query_task_stats: '任务执行统计',
}

export const SCENE_ZH: Record<string, string> = {
  risk_fill: '风险填报',
  cash_guarantee: '现金保障',
  kpi_fill: '经营者考核',
  ms_feedback: '里程碑反馈',
  lamp_adjust: '亮灯调整',
  report: '投后报告',
  generic: '通用任务',
}

export const TODO_STATUS_ZH: Record<string, string> = {
  pending: '待处理',
  feedback_submitted: '已反馈',
  na_pending: '待确认',
  submitted: '已提交',
  completed: '已完成',
  na_closed: '已关闭',
}

export function toolZh(name: string): string {
  return TOOL_ZH[name] ?? name
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 历史栏日期分组标签 */
export function dayGroup(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  const dayStart = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diff = Math.floor((dayStart(now) - dayStart(d)) / 86400000)
  if (diff <= 0) return '今天'
  if (diff === 1) return '昨天'
  if (diff < 7) return '近 7 天'
  return '更早'
}

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

/** 现金保障倍数与亮灯（契约口径：≤3 红 / ≤6 黄 / >6 绿） */
export function cashRatio(v: { avail_cash: number; pooled_fund: number; avail_credit: number; monthly_outflow: number }): number {
  if (!v.monthly_outflow) return 0
  return (v.avail_cash + v.pooled_fund + v.avail_credit) / v.monthly_outflow
}

export function cashLamp(ratio: number): 'r' | 'y' | 'g' {
  if (ratio <= 3) return 'r'
  if (ratio <= 6) return 'y'
  return 'g'
}
