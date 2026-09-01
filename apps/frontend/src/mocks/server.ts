// mock 数据与 mock SSE：vite dev 时 VITE_MOCK=1 或 URL ?mock=1 启用
// 按契约形状提供登录/会话/chat SSE/todos/dashboard/risk-fills/kpi/cash/kb 等 mock
import type { DashboardSummary, PlatformEvent, RiskItem, SessionListItem, TodoItem } from '@/types'
import type { SseHandlers } from '@/transport/sse'
import { cashLamp, cashRatio } from '@/utils'

const sessionId = 'sess-demo-01'
const hq01 = { user_id: 'u-hq01', username: 'hq01', display_name: '李工', role: 'hq_finance' as const, company: '信投股份' }
const companies = [
  '信投智造','信投新能','信投医疗','信投数科','信投物流','信投环保',
  '信投半导','信投云联','信投金服','信投教育','信投文旅',
]

function res<T>(data: T) {
  return Promise.resolve(data)
}

function delay(ms: number) {
  return new Promise<void>((r) => setTimeout(r, ms))
}

function now() {
  return new Date().toISOString()
}

// === mock 事件总线：chat 流结束后保留监听（模拟常驻事件通道），供组件 submit 回推 ===
const listeners = new Map<string, SseHandlers>()
let seqCtr = 200
// component_id → 注册信息（submit 回推时取 kind/props）
const compRegistry = new Map<string, { sid: string; kind: string; props: Record<string, unknown> }>()

function nextSeq() {
  return ++seqCtr
}

function pushEvent(sid: string, type: string, data: Record<string, unknown>): number {
  const h = listeners.get(sid)
  const seq = nextSeq()
  h?.onEvent({ seq, type, time: now(), data })
  return seq
}

/** 推送一段 assistant 流式回复（chunk×N + message 固化） */
function pushAssistant(sid: string, content: string) {
  const seqs: number[] = []
  for (const ch of content) seqs.push(pushEvent(sid, 'assistant/chunk', { delta: ch, version: 1 }))
  pushEvent(sid, 'assistant/message', { content, source_event_seqs: seqs, version: 1 })
}

// === 登录 ===
async function mockLogin(body: unknown) {
  const { username } = body as { username?: string }
  if (username === 'hq01') return res({ token: 'tok-hq01', user: hq01 })
  const invIdx = (username?.match(/^inv(\d+)$/) ?? [])[1]
  if (invIdx) {
    const idx = Math.max(1, Math.min(11, Number(invIdx))) - 1
    return res({ token: `tok-${username}`, user: { user_id: `u-${username}`, username, display_name: `${username} · 被投财务`, role: 'investee_finance' as const, company: companies[idx] } })
  }
  const e = new Error('用户名或密码错误')
  ;(e as any).status = 401
  throw e
}

// === 会话 ===
const sessions: SessionListItem[] = [
  { session_id: sessionId, user_id: 'u-hq01', title: '发起风险填报', domain: 'general', status: 'active', created_at: '2026-08-15T09:30:00+08:00', updated_at: '2026-08-16T10:00:00+08:00', last_message: '已下发风险填报任务', pending_interaction: true },
  { session_id: 'sess-02', user_id: 'u-hq01', title: '现金保障倍数试算', domain: 'general', status: 'active', created_at: '2026-08-14T14:10:00+08:00', updated_at: '2026-08-14T15:20:00+08:00', last_message: '信投数科 倍数 4.2，黄灯', pending_interaction: false },
  { session_id: 'sess-03', user_id: 'u-hq01', title: '生成投后报告', domain: 'general', status: 'active', created_at: '2026-08-10T11:00:00+08:00', updated_at: '2026-08-10T11:45:00+08:00', last_message: '报告已生成', pending_interaction: false },
]

// === SSE 帧序列（seq 全局单调递增，支持同会话多轮） ===
function buildMockChatEvents(message: string): PlatformEvent[] {
  const evts: PlatformEvent[] = [
    { seq: nextSeq(), type: 'turn/start', time: now(), data: { turn: 1 } },
    { seq: nextSeq(), type: 'step/start', time: now(), data: { turn: 1, step: 1 } },
    { seq: nextSeq(), type: 'step/end', time: now(), data: { turn: 1, step: 1 } },
    { seq: nextSeq(), type: 'step/start', time: now(), data: { turn: 1, step: 2 } },
    { seq: nextSeq(), type: 'step/end', time: now(), data: { turn: 1, step: 2 } },
  ]
  const callSeq = nextSeq()
  evts.push(
    { seq: callSeq, type: 'tool/call', time: now(), data: { call_id: 'tc-1', name: 'query_task_stats', arguments: {}, version: 1 } },
    { seq: nextSeq(), type: 'tool/result', time: now(), data: { call_id: 'tc-1', name: 'query_task_stats', content: '当前下发任务 8 个，本周完成 5 个，完成率 62%', is_error: false, refs: [callSeq], version: 1 } },
  )
  const reply = `收到你的请求：“${message}”。我来帮你处理。根据当前统计，下发任务 8 个，本周完成 5 个，完成率 62%。`
  const chunkSeqs: number[] = []
  for (const ch of reply) {
    const s = nextSeq()
    chunkSeqs.push(s)
    evts.push({ seq: s, type: 'assistant/chunk', time: now(), data: { turn: 1, step: 3, delta: ch, version: 1 } })
  }
  evts.push({ seq: nextSeq(), type: 'assistant/message', time: now(), data: { turn: 1, step: 3, content: reply, source_event_seqs: chunkSeqs, version: 1 } })
  evts.push({ seq: nextSeq(), type: 'turn/end', time: now(), data: { turn: 1, reason: 'completed' } })
  // 若消息含“风险填报”，追加 component/request
  if (message.includes('风险') && (message.includes('填报') || message.includes('填写'))) {
    const cid = `comp-risk-${seqCtr}`
    const props = { period: '2026-08', companies }
    compRegistry.set(cid, { sid: sessionId, kind: 'risk-dispatch-confirm', props })
    evts.push({
      seq: nextSeq(), type: 'component/request', time: now(),
      data: { component_id: cid, kind: 'risk-dispatch-confirm', kind_version: 1, props, interrupt_id: 'int-1', version: 1 },
    })
  }
  return evts
}

// === 待办 ===
const todoAssignee: TodoItem[] = [
  { todo_id: 'td-1', task_id: 't-1', kind: 'action', scene: 'risk_fill', title: '风险预警财务指标填报', sub: '归属期：2026-08 · 待反馈', status: 'pending', lamp: 'r', ref: { batch_id: 'rb-1' }, dispatcher_name: '李工', due: '2026-08-20T17:00:00+08:00', created_at: '2026-08-15T09:00:00+08:00', updated_at: '2026-08-15T09:00:00+08:00' },
  { todo_id: 'td-2', task_id: 't-2', kind: 'action', scene: 'cash_guarantee', title: '现金保障倍数填报', sub: '信投数科 · 本月还有 3 项', status: 'pending', lamp: 'r', ref: { form_id: 'cf-1' }, dispatcher_name: '李工', due: '2026-08-22T17:00:00+08:00', created_at: '2026-08-14T14:00:00+08:00', updated_at: '2026-08-14T14:00:00+08:00' },
  { todo_id: 'td-4', task_id: 't-4', kind: 'action', scene: 'ms_feedback', title: '里程碑反馈 · 数字化转型', sub: '信投数科 · 进行中 60%', status: 'pending', lamp: 'g', ref: { feedback_id: 'fb-1', company: '信投数科' }, dispatcher_name: '李工', due: '2026-08-25T17:00:00+08:00', created_at: '2026-08-15T10:00:00+08:00', updated_at: '2026-08-15T10:00:00+08:00' },
  { todo_id: 'td-5', task_id: 't-5', kind: 'action', scene: 'kpi_fill', title: '经营者考核指标填报', sub: '归属期：2026-08 · 4 项指标', status: 'pending', lamp: null, ref: { batch_id: 'kb-1', company: '信投数科' }, dispatcher_name: '李工', due: '2026-08-28T17:00:00+08:00', created_at: '2026-08-15T11:00:00+08:00', updated_at: '2026-08-15T11:00:00+08:00' },
]

const todoDispatcher: TodoItem[] = [
  { todo_id: 'td-3', task_id: 't-1', kind: 'review', scene: 'risk_fill', title: '信投数科 风险预警指标审批', sub: '待审批 · 来自信投数科填报', status: 'pending', lamp: 'y', ref: { batch_id: 'rb-1', company: '信投数科' }, dispatcher_name: '李工', due: null, created_at: '2026-08-15T09:30:00+08:00', updated_at: '2026-08-15T09:30:00+08:00' },
  { todo_id: 'td-6', task_id: 't-6', kind: 'na_confirm', scene: 'generic', title: '信投文旅 申请标记「不涉及」', sub: '通用任务 · 等待确认', status: 'pending', lamp: null, ref: { task_id: 't-6' }, dispatcher_name: '李工', due: null, created_at: '2026-08-16T09:00:00+08:00', updated_at: '2026-08-16T09:00:00+08:00' },
]

// === Dashboard ===
const dashboard: DashboardSummary = {
  overview: { open_tasks: 8, completed_7d: 5, completion_rate: 0.625, overdue: 2 },
  by_scene: [
    { scene: 'risk_fill', total: 12, done: 5 },
    { scene: 'cash_guarantee', total: 3, done: 1 },
    { scene: 'kpi_fill', total: 4, done: 3 },
    { scene: 'ms_feedback', total: 6, done: 2 },
    { scene: 'report', total: 1, done: 1 },
    { scene: 'generic', total: 2, done: 0 },
  ],
  todo_funnel: [
    { status: 'pending', count: 8 },
    { status: 'feedback_submitted', count: 3 },
    { status: 'na_pending', count: 1 },
    { status: 'submitted', count: 4 },
    { status: 'completed', count: 12 },
    { status: 'na_closed', count: 1 },
  ],
  risk_board: {
    batch_id: 'rb-1',
    period: '2026-08',
    companies: companies.map((c) => ({ company: c, status: Math.random() > 0.7 ? 'reviewed' : Math.random() > 0.5 ? 'filled' : 'unfilled' })),
    lamps: { r: 4, y: 7, g: 13 },
  },
  trend_14d: Array.from({ length: 14 }, (_, i) => {
    const d = new Date()
    d.setDate(d.getDate() - (13 - i))
    return { date: d.toISOString().slice(5, 10), created: Math.round(Math.random() * 4), completed: Math.round(Math.random() * 3) }
  }),
}

// === 风险填报 16 项模板 ===
const RISK_TPL: [string, string][] = [
  ['资产负债率', '65%'], ['流动比率', '1.50'], ['速动比率', '1.00'], ['现金保障倍数', '6.0'],
  ['应收账款周转率', '4.5'], ['存货周转率', '3.2'], ['净资产收益率', '8%'], ['营业收入增长率', '12%'],
  ['净利润增长率', '10%'], ['成本费用占营收比', '85%'], ['带息负债比率', '40%'], ['经营现金净流量', '5000'],
  ['投资现金净流量', '-2000'], ['筹资现金净流量', '-1000'], ['国有资本保值增值率', '105%'], ['两金占流动资产比', '45%'],
]

function buildRiskItems(): RiskItem[] {
  return RISK_TPL.map(([name, budget], i) => ({
    idx: i + 1,
    name,
    lamp: 'g',
    fields: [
      { k: '预算值', v: budget, pf: true },
      { k: '实际值', v: '' },
      { k: '异常说明', v: '' },
    ],
  }))
}

// === 知识库树 ===
const kbSources = [
  { kb_id: 'kb-ent', name: '企业知识库', parent_id: null, kb_type: 'internal' as const },
  { kb_id: 'kb-ent-1', name: '信投股份制度库', parent_id: 'kb-ent', kb_type: 'internal' as const },
  { kb_id: 'kb-dept', name: '部门知识库', parent_id: null, kb_type: 'internal' as const },
  { kb_id: 'kb-dept-1', name: '财务部共享', parent_id: 'kb-dept', kb_type: 'internal' as const },
  { kb_id: 'kb-proj', name: '项目知识库', parent_id: null, kb_type: 'internal' as const },
  { kb_id: 'kb-proj-1', name: '信投股份', parent_id: 'kb-proj', kb_type: 'internal' as const },
  { kb_id: 'kb-proj-2', name: '信息建设', parent_id: 'kb-proj', kb_type: 'internal' as const },
  { kb_id: 'kb-ext', name: '外部知识库', parent_id: null, kb_type: 'external' as const },
  { kb_id: 'kb-personal', name: '个人知识库', parent_id: null, kb_type: 'internal' as const },
]

// === 路由分发 ===
export async function mockApi<T>(method: string, path: string, body?: unknown): Promise<T> {
  await delay(200)
  const p = path.replace(/^\//, '')
  const pn = p.split('?')[0]
  if (method === 'POST' && pn === 'auth/login') return (await mockLogin(body)) as T
  if (method === 'POST' && pn === 'auth/logout') return res({ ok: true }) as T
  if (method === 'GET' && pn === 'auth/me') return res(hq01) as T

  if (method === 'GET' && pn.startsWith('sessions')) {
    if (pn === 'sessions') return res({ items: sessions, total: sessions.length }) as T
    const evm = pn.match(/^sessions\/(?<id>[^/]+)\/events/)
    if (evm) {
      // JSON 回放（SSE 形态在 streamChat/resumeEvents）
      return res({ items: [], has_more: false }) as T
    }
    const m = pn.match(/^sessions\/(?<id>[^/]+)$/)
    if (m) {
      const s = sessions.find((x) => x.session_id === m.groups!.id)
      return res({ ...(s ?? sessions[0]), stats: {} }) as T
    }
  }
  if (method === 'POST' && pn === 'sessions') return res(sessions[0]) as T
  if (method === 'POST' && /^sessions\/[^/]+\/cancel$/.test(pn)) return res({ ok: true }) as T

  // 组件 update：落事件由后端负责，mock 仅回执
  const upd = pn.match(/^sessions\/(?<sid>[^/]+)\/components\/(?<cid>[^/]+)\/update$/)
  if (method === 'POST' && upd) return res({ ok: true, event_seq: nextSeq() }) as T
  // 组件 submit：回执后回推事件族
  const sub = pn.match(/^sessions\/(?<sid>[^/]+)\/components\/(?<cid>[^/]+)\/submit$/)
  if (method === 'POST' && sub) {
    const sid = sub.groups!.sid
    const cid = sub.groups!.cid
    const { action, values } = (body ?? {}) as { action?: string; values?: Record<string, unknown> }
    const reg = compRegistry.get(cid)
    setTimeout(() => {
      const summary = values ? `已提交：${JSON.stringify(values).slice(0, 60)}` : action === 'cancel' ? '已取消' : '已提交'
      pushEvent(sid, 'component/submit', {
        component_id: cid,
        action: action === 'cancel' ? 'cancelled' : 'submit',
        ...(values ? { values } : {}),
        summary,
        version: 1,
      })
      if (reg?.kind === 'risk-dispatch-confirm' && action === 'submit') {
        const list = (values?.companies as string[]) ?? (reg.props.companies as string[]) ?? []
        const period = String(values?.period ?? reg.props.period ?? '')
        pushEvent(sid, 'risk/fill-start', { batch_id: 'rb-1', period, companies: list, version: 1 })
        pushAssistant(sid, `已下发 ${list.length} 家风险填报任务（批次 rb-1，归属期 ${period}），进度可在会话看板卡与屏2追踪。`)
      }
      if (reg?.kind === 'cash-guarantee-form' && action === 'submit') {
        const cv = (values?.values ?? {}) as { avail_cash: number; pooled_fund: number; avail_credit: number; monthly_outflow: number }
        const ratio = cashRatio(cv)
        const lamp = cashLamp(ratio)
        const lampZh = lamp === 'r' ? '红灯' : lamp === 'y' ? '黄灯' : '绿灯'
        const cashSummary = `现金保障倍数 ${ratio.toFixed(2)}（${lampZh}），已提交`
        pushEvent(sid, 'cash/form-submit', { form_id: reg.props.form_id ?? '', values: cv, ratio, lamp, summary: cashSummary, version: 1 })
        pushAssistant(sid, cashSummary)
      }
    }, 0)
    return res({ ok: true }) as T
  }

  if (method === 'GET' && pn === 'todos') {
    const box = new URLSearchParams(p.split('?')[1] ?? '').get('box') ?? 'assignee'
    return res({ items: box === 'assignee' ? todoAssignee : todoDispatcher }) as T
  }
  if (method === 'POST' && /^todos\/[^/]+\/feedback$/.test(pn)) return res({ ok: true }) as T
  if (method === 'POST' && /^todos\/[^/]+\/na$/.test(pn)) return res({ ok: true }) as T
  if (method === 'POST' && /^todos\/[^/]+\/na-confirm$/.test(pn)) return res({ ok: true }) as T
  if (method === 'POST' && /^todos\/[^/]+\/na-reject$/.test(pn)) return res({ ok: true }) as T
  if (method === 'POST' && /^todos\/[^/]+\/complete$/.test(pn)) return res({ ok: true }) as T

  if (method === 'GET' && pn === 'dashboard/summary') return res(dashboard) as T

  // 风险填报
  if (method === 'GET' && pn.startsWith('risk-fills')) {
    if (pn === 'risk-fills') return res([{ batch_id: 'rb-1', period: '2026-08', dispatcher_id: 'u-hq01', status: 'collecting', created_at: '2026-08-15T09:00:00+08:00' }]) as T
    const rm = pn.match(/^risk-fills\/(?<bid>[^/]+)\/reports\/(?<company>[^/]+)$/)
    if (rm) {
      const company = decodeURIComponent(rm.groups!.company)
      return res({
        report_id: `rr-${company}`, batch_id: rm.groups!.bid, company,
        status: 'unfilled', lamp_r: 0, lamp_y: 0, lamp_g: RISK_TPL.length, items: buildRiskItems(),
      }) as T
    }
    const bm = pn.match(/^risk-fills\/(?<bid>[^/]+)$/)
    if (bm) {
      return res({
        batch_id: 'rb-1', period: '2026-08', dispatcher_id: 'u-hq01', status: 'collecting', created_at: '2026-08-15T09:00:00+08:00',
        reports: companies.map((c, i) => ({ report_id: `rr-${i}`, batch_id: 'rb-1', company: c, status: Math.random() > 0.7 ? 'reviewed' : Math.random() > 0.5 ? 'filled' : 'unfilled', lamp_r: 0, lamp_y: 0, lamp_g: 0, items: [] })),
      }) as T
    }
  }
  if (method === 'POST' && pn === 'risk-fills') return res({ batch_id: 'rb-1', period: '2026-08', dispatcher_id: 'u-hq01', status: 'collecting', created_at: '2026-08-15T09:00:00+08:00' }) as T
  if (method === 'PUT' && /risk-fills\/[^/]+\/reports\/[^/]+\/items$/.test(pn)) return res({ ok: true }) as T
  if (method === 'POST' && /risk-fills\/[^/]+\/reports\/[^/]+\/submit$/.test(pn)) return res({ ok: true }) as T
  if (method === 'POST' && /risk-fills\/[^/]+\/reports\/[^/]+\/review$/.test(pn)) return res({ ok: true }) as T

  // 现金保障
  const cg = pn.match(/^cash-guarantees\/(?<id>[^/]+)$/)
  if (method === 'GET' && cg) {
    const values = { avail_cash: 1200, pooled_fund: 800, avail_credit: 2000, monthly_outflow: 900 }
    const ratio = cashRatio(values)
    return res({
      form_id: cg.groups!.id, company: '信投数科', period: '2026-08', ...values,
      ratio: Number(ratio.toFixed(2)), lamp: cashLamp(ratio), status: 'draft',
    }) as T
  }
  if (method === 'GET' && pn === 'cash-guarantees') return res([]) as T
  if (method === 'PUT' && /^cash-guarantees\/[^/]+$/.test(pn)) return res({ ok: true }) as T
  if (method === 'POST' && /^cash-guarantees\/[^/]+\/submit$/.test(pn)) return res({ ok: true }) as T
  if (method === 'POST' && /^cash-guarantees\/[^/]+\/review$/.test(pn)) return res({ ok: true }) as T

  // 经营者考核
  const kc = pn.match(/^kpi\/batches\/(?<bid>[^/]+)\/companies\/(?<company>[^/]+)$/)
  if (method === 'GET' && kc) {
    return res({
      indicators: [
        { indicator_id: 'ki-1', dim: '经济效益', name: '营业收入', kpi_type: '定量', content: '', base_score: '20', max_score: '25' },
        { indicator_id: 'ki-2', dim: '经济效益', name: '净利润', kpi_type: '定量', content: '', base_score: '20', max_score: '25' },
        { indicator_id: 'ki-3', dim: '改革创新', name: '数字化转型进度', kpi_type: '定性', content: '', base_score: '15', max_score: '20' },
        { indicator_id: 'ki-4', dim: '风控合规', name: '合规经营', kpi_type: '定性', content: '', base_score: '15', max_score: '20' },
      ],
      milestones: [
        { milestone_id: 'ms-1', indicator_id: 'ki-3', content: '完成核心系统上云', plan_date: '2026-09-30', material: '验收报告' },
        { milestone_id: 'ms-2', indicator_id: 'ki-3', content: '数据中台一期上线', plan_date: '2026-11-30', material: '上线证明' },
      ],
    }) as T
  }
  if (method === 'PUT' && /kpi\/batches\/[^/]+\/companies\/[^/]+\/(indicators|milestones)$/.test(pn)) return res({ ok: true }) as T
  if (method === 'POST' && /kpi\/batches\/[^/]+\/companies\/[^/]+\/(submit|review)$/.test(pn)) return res({ ok: true }) as T

  // 里程碑反馈
  if (method === 'GET' && pn === 'kpi/ms-feedbacks') {
    return res({
      items: [
        { feedback_id: 'fb-1', company: '信投数科', milestone_content: '数字化转型里程碑 · 数据中台一期上线', status: '进行中', progress: 60, lamp: 'g' },
      ],
    }) as T
  }
  if (method === 'PUT' && /^kpi\/ms-feedbacks\/[^/]+$/.test(pn)) return res({ ok: true }) as T
  if (method === 'POST' && /^kpi\/ms-feedbacks\/[^/]+\/(submit|review)$/.test(pn)) return res({ ok: true }) as T

  // 知识库
  if (method === 'GET' && pn === 'kb/sources') return res({ items: kbSources }) as T

  throw new Error(`Mock 未实现：${method} ${path}`)
}

export async function mockSse(
  sessionId: string,
  ctx: { kind: 'chat'; body: { message: string; kb_ids?: string[] } } | { kind: 'events'; afterSeq: number },
  handlers: SseHandlers,
) {
  await delay(150)
  listeners.set(sessionId, handlers)
  handlers.onStatus?.('open')
  const evts: PlatformEvent[] =
    ctx.kind === 'chat'
      ? buildMockChatEvents(ctx.body.message)
      : buildMockChatEvents('续流').filter((e) => e.seq > ctx.afterSeq)
  for (const e of evts) {
    await delay(ctx.kind === 'chat' ? 40 : 20)
    handlers.onEvent(e)
  }
  handlers.onStatus?.('closed')
}
