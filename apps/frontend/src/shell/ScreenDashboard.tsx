// 看板（底部抽屉承载）：「任务看板 / 数据看板」双 Tab 切换（手绘 SVG，不引图表库）
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/transport/api'
import type { MetricsOverview } from '@/types'
import { TODO_STATUS_ZH } from '@/utils'

/** token 用量展示：万 / 亿缩写 */
function fmtTokens(n: number): string {
  if (n >= 100_000_000) return `${(n / 100_000_000).toFixed(2)} 亿`
  if (n >= 10_000) return `${(n / 10_000).toFixed(1)} 万`
  return String(n)
}

export default function ScreenDashboard() {
  const [tab, setTab] = useState<'task' | 'data'>('task')
  const [m, setM] = useState<MetricsOverview | null>(null)
  const load = useCallback(() => {
    api<MetricsOverview>('GET', '/metrics/overview').then(setM).catch(() => {})
  }, [])
  useEffect(load, [load])

  const refresh = <button className="dash-refresh" onClick={load}>刷新</button>
  const tb = m?.task_board
  const dd = m?.data_board

  return (
    <div className="dash-body">
      <div className="dash-tabs">
        <div className={`dash-tab ${tab === 'task' ? 'on' : ''}`} onClick={() => setTab('task')}>
          我的任务看板
        </div>
        <div className={`dash-tab ${tab === 'data' ? 'on' : ''}`} onClick={() => setTab('data')}>
          数据看板
        </div>
        {refresh}
      </div>
      {tab === 'task' ? <TaskBoard tb={tb} /> : <DataBoard dd={dd} />}
    </div>
  )
}

type TaskBoardData = MetricsOverview['task_board'] | undefined

function TaskBoard({ tb }: { tb: TaskBoardData }) {
  const maxFunnel = Math.max(1, ...(tb?.assigned_funnel.map((x) => x.count) ?? [1]))
  return (
    <div className="dash-grid">
      <div className="dash-card span-6">
        <h3>派发给我的</h3>
        <ProgressRow done={tb?.assigned.done ?? 0} total={tb?.assigned.total ?? 0} />
        <div className="ov-row">
          <Stat label="待办" v={tb?.assigned.total} />
          <Stat label="已完成" v={tb?.assigned.done} cls="ok-v" />
          <Stat label="进行中" v={tb?.assigned.open} />
          <Stat label="逾期" v={tb?.assigned.overdue} cls="warn-v" />
        </div>
      </div>

      <div className="dash-card span-6">
        <h3>我派发的</h3>
        <ProgressRow done={tb?.dispatched.done ?? 0} total={tb?.dispatched.total ?? 0} />
        <div className="ov-row">
          <Stat label="发起任务" v={tb?.dispatched.tasks} />
          <Stat label="待办" v={tb?.dispatched.total} />
          <Stat label="已完成" v={tb?.dispatched.done} cls="ok-v" />
          <Stat label="进行中" v={tb?.dispatched.open} />
        </div>
      </div>

      <div className="dash-card span-7">
        <h3>近 14 天待办趋势</h3>
        <TrendChart trend={tb?.trend_14d ?? []} />
        <div className="trend-legend">
          <span>
            <i style={{ background: 'var(--amber)' }} />
            新建
          </span>
          <span>
            <i style={{ background: 'var(--green)' }} />
            完成
          </span>
        </div>
      </div>

      <div className="dash-card span-5">
        <h3>AI 产出</h3>
        <div className="ov-row" style={{ marginBottom: 14 }}>
          <Stat label="生成文档" v={tb?.docs} />
          <Stat label="保存会议" v={tb?.meetings} />
        </div>
        <div className="scene-row">
          <span className="s-name">Token 消耗</span>
          <span className="s-val big">{tb ? fmtTokens(tb.tokens) : '—'}</span>
        </div>
        <div className="todo-empty" style={{ marginTop: 8 }}>统计范围：我的对话与执行记录</div>
      </div>

      <div className="dash-card span-12">
        <h3>我的待办状态分布</h3>
        {(tb?.assigned_funnel ?? []).map((f) => (
          <div className="funnel-row" key={f.status}>
            <span className="f-name">{TODO_STATUS_ZH[f.status] ?? f.status}</span>
            <span className="f-bar" style={{ width: `${(f.count / maxFunnel) * 100}%` }}>
              {f.count}
            </span>
          </div>
        ))}
        {tb && tb.assigned_funnel.length === 0 && <div className="todo-empty">暂无数据</div>}
      </div>
    </div>
  )
}

type DataBoardData = MetricsOverview['data_board'] | undefined

function DataBoard({ dd }: { dd: DataBoardData }) {
  const maxRuns = Math.max(1, ...(dd?.custom_cards.map((c) => c.runs) ?? [1]))
  return (
    <div className="dash-grid">
      <div className="dash-card span-7">
        <h3>业务系统使用</h3>
        {(dd?.custom_cards ?? []).map((c) => (
          <div className="scene-row" key={c.id}>
            <span className="s-name">{c.name}</span>
            <span className="s-bar">
              <i style={{ width: `${(c.runs / maxRuns) * 100}%` }} />
            </span>
            <span className="s-val">{c.runs} 次</span>
          </div>
        ))}
        {dd && dd.custom_cards.length === 0 && <div className="todo-empty">暂无自定义能力，可在首页添加</div>}
      </div>

      <div className="dash-card span-5">
        <h3>知识库</h3>
        <div className="ov-row">
          <Stat label="内部" v={dd?.kb.internal} />
          <Stat label="外部" v={dd?.kb.external} />
          <Stat label="全部" v={dd?.kb.total} cls="ok-v" />
        </div>
      </div>

      <div className="dash-card span-12">
        <h3>应用使用</h3>
        <div className="ov-row">
          <Stat label="会话数" v={dd?.app_usage.sessions} />
          <Stat label="消息数" v={dd?.app_usage.messages} cls="ok-v" />
          <Stat label="执行次数" v={dd?.app_usage.exec_runs} />
          <Stat label="活跃天数" v={dd?.app_usage.active_days} cls="warn-v" />
        </div>
      </div>
    </div>
  )
}

function Stat({ label, v, cls }: { label: string; v: number | undefined; cls?: string }) {
  return (
    <div className="ov-item">
      <div className="k">{label}</div>
      <div className={`v ${cls ?? ''}`}>{v ?? '—'}</div>
    </div>
  )
}

function ProgressRow({ done, total }: { done: number; total: number }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div className="scene-row" style={{ marginBottom: 14 }}>
      <span className="s-name">完成率</span>
      <span className="s-bar">
        <i style={{ width: `${pct}%` }} />
      </span>
      <span className="s-val">
        {pct}% · {done}/{total}
      </span>
    </div>
  )
}

function TrendChart({ trend }: { trend: { date: string; created: number; completed: number }[] }) {
  const W = 560
  const H = 180
  const P = 24
  if (trend.length === 0) return <div className="todo-empty">暂无数据</div>
  const max = Math.max(1, ...trend.map((t) => Math.max(t.created, t.completed)))
  const x = (i: number) => P + (i * (W - 2 * P)) / (trend.length - 1)
  const y = (v: number) => H - P - (v / max) * (H - 2 * P)
  const pts = (key: 'created' | 'completed') => trend.map((t, i) => `${x(i)},${y(t[key])}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto' }}>
      {/* y 轴 0/max 虚线网格 */}
      {[0, max].map((v) => (
        <g key={v}>
          <line x1={P} x2={W - P} y1={y(v)} y2={y(v)} stroke="var(--rim-line)" strokeDasharray="4 4" />
          <text x={P - 4} y={y(v) + 3} fontSize="9" fill="var(--ink-30)" textAnchor="end">
            {v}
          </text>
        </g>
      ))}
      <polyline points={pts('created')} fill="none" stroke="var(--amber)" strokeWidth="2" />
      <polyline points={pts('completed')} fill="none" stroke="var(--green)" strokeWidth="2" />
      {trend.map((t, i) => (
        <g key={t.date}>
          <circle cx={x(i)} cy={y(t.created)} r="2.5" fill="var(--amber)" />
          <circle cx={x(i)} cy={y(t.completed)} r="2.5" fill="var(--green)" />
          {i % 3 === 0 && (
            <text x={x(i)} y={H - 6} fontSize="9" fill="var(--ink-30)" textAnchor="middle">
              {t.date}
            </text>
          )}
        </g>
      ))}
    </svg>
  )
}
