// 屏2 看板：总览/场景分布/待办漏斗/风险专项/14 天趋势（手绘 SVG，不引图表库）
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/transport/api'
import type { DashboardSummary } from '@/types'
import { SCENE_ZH, TODO_STATUS_ZH } from '@/utils'
import { KanbanGrid, LampStats } from '@/plugins/KanbanCard'

export default function ScreenDashboard() {
  const [data, setData] = useState<DashboardSummary | null>(null)
  const load = useCallback(() => {
    api<DashboardSummary>('GET', '/dashboard/summary')
      .then(setData)
      .catch(() => {})
  }, [])
  useEffect(load, [load])

  const refresh = <button className="dash-refresh" onClick={load}>刷新</button>
  const maxScene = Math.max(1, ...(data?.by_scene.map((x) => x.total) ?? [1]))
  const maxFunnel = Math.max(1, ...(data?.todo_funnel.map((x) => x.count) ?? [1]))

  return (
    <section className="screen screen-dash" id="screen-dash">
      <div className="dash-body">
        <div className="dash-grid">
          <div className="dash-card span-12">
            <h3>
              总览
              <span className="sub">任务全局</span>
              {refresh}
            </h3>
            <div className="ov-row">
              <div className="ov-item">
                <div className="k">下发中</div>
                <div className="v">{data?.overview.open_tasks ?? '—'}</div>
              </div>
              <div className="ov-item">
                <div className="k">完成率</div>
                <div className="v">{data ? `${Math.round(data.overview.completion_rate * 100)}%` : '—'}</div>
              </div>
              <div className="ov-item">
                <div className="k">近 7 天完成</div>
                <div className="v ok-v">{data?.overview.completed_7d ?? '—'}</div>
              </div>
              <div className="ov-item">
                <div className="k">逾期</div>
                <div className="v warn-v">{data?.overview.overdue ?? '—'}</div>
              </div>
            </div>
          </div>

          <div className="dash-card span-7">
            <h3>
              按场景分布
              {refresh}
            </h3>
            {(data?.by_scene ?? []).map((s) => (
              <div className="scene-row" key={s.scene}>
                <span className="s-name">{SCENE_ZH[s.scene] ?? s.scene}</span>
                <span className="s-bar">
                  <i style={{ width: `${(s.total / maxScene) * 100}%` }} />
                  <em style={{ width: `${s.total > 0 ? (s.done / s.total) * 100 : 0}%` }} />
                </span>
                <span className="s-val">
                  {s.done}/{s.total}
                </span>
              </div>
            ))}
          </div>

          <div className="dash-card span-5">
            <h3>
              待办漏斗
              {refresh}
            </h3>
            {(data?.todo_funnel ?? []).map((f) => (
              <div className="funnel-row" key={f.status}>
                <span className="f-name">{TODO_STATUS_ZH[f.status] ?? f.status}</span>
                <span className="f-bar" style={{ width: `${(f.count / maxFunnel) * 100}%` }}>
                  {f.count}
                </span>
              </div>
            ))}
          </div>

          <div className="dash-card span-7">
            <h3>
              风险填报{data?.risk_board ? ` · ${data.risk_board.period}` : ''}
              {refresh}
            </h3>
            {data?.risk_board ? (
              <>
                <div className="kanban-stats" style={{ marginBottom: 12 }}>
                  <span className="kb-stat">
                    <b>{data.risk_board.companies.length}</b>企业
                  </span>
                  <span className="kb-stat">
                    <b className="y">{data.risk_board.companies.filter((c) => c.status === 'filled').length}</b>已填
                  </span>
                  <span className="kb-stat">
                    <b className="g">{data.risk_board.companies.filter((c) => c.status === 'reviewed').length}</b>已审计
                  </span>
                  <LampStats lamps={data.risk_board.lamps} />
                </div>
                <KanbanGrid companies={data.risk_board.companies} />
              </>
            ) : (
              <div className="todo-empty">暂无填报批次</div>
            )}
          </div>

          <div className="dash-card span-5">
            <h3>
              近 14 天趋势
              {refresh}
            </h3>
            <TrendChart trend={data?.trend_14d ?? []} />
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
        </div>
      </div>
    </section>
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
          <line x1={P} x2={W - P} y1={y(v)} y2={y(v)} stroke="rgba(255,255,255,.1)" strokeDasharray="4 4" />
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
