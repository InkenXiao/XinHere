// kanban-card@1：风险批次进度网格（会话内追踪卡；网格展示件复用于屏2看板）
import type { ComponentDefinition, ComponentProps } from '@/registry/types'
import type { PlatformEvent } from '@/types'

interface CoState {
  status: 'unfilled' | 'filled' | 'reviewed'
  lamp_r: number
  lamp_y: number
  lamp_g: number
}

interface S {
  batch_id: string
  period: string
  companies: Record<string, CoState>
}

const ST_ZH = { unfilled: '未填', filled: '已填', reviewed: '已审' } as const

function match(evt: PlatformEvent) {
  if (evt.type === 'risk/fill-start') {
    return { id: String(evt.data.batch_id), role: 'start' as const }
  }
  if (evt.type === 'risk/report-update' || evt.type === 'risk/submit' || evt.type === 'risk/review') {
    return { id: String(evt.data.batch_id), role: 'update' as const }
  }
  return null
}

function reduce(s: S | undefined, evt: PlatformEvent): S {
  if (evt.type === 'risk/fill-start') {
    const companies: Record<string, CoState> = {}
    for (const c of (evt.data.companies as string[]) ?? []) {
      companies[c] = { status: 'unfilled', lamp_r: 0, lamp_y: 0, lamp_g: 0 }
    }
    return { batch_id: String(evt.data.batch_id), period: String(evt.data.period ?? ''), companies }
  }
  if (!s) throw new Error('kanban-card 状态缺失')
  if (evt.type === 'risk/report-update') {
    const c = String(evt.data.company)
    return {
      ...s,
      companies: {
        ...s.companies,
        [c]: {
          status: evt.data.status as CoState['status'],
          lamp_r: (evt.data.lamp_r as number) ?? 0,
          lamp_y: (evt.data.lamp_y as number) ?? 0,
          lamp_g: (evt.data.lamp_g as number) ?? 0,
        },
      },
    }
  }
  if (evt.type === 'risk/submit') {
    const c = String(evt.data.company)
    const cur = s.companies[c] ?? { status: 'unfilled', lamp_r: 0, lamp_y: 0, lamp_g: 0 }
    return { ...s, companies: { ...s.companies, [c]: { ...cur, status: 'filled' } } }
  }
  if (evt.type === 'risk/review') {
    const c = String(evt.data.company)
    const cur = s.companies[c] ?? { status: 'filled', lamp_r: 0, lamp_y: 0, lamp_g: 0 }
    return {
      ...s,
      companies: { ...s.companies, [c]: { ...cur, status: evt.data.approve ? 'reviewed' : 'unfilled' } },
    }
  }
  return s
}

/** 公司进度网格（屏2 risk_board 复用） */
export function KanbanGrid({ companies }: { companies: { company: string; status: 'unfilled' | 'filled' | 'reviewed' }[] }) {
  return (
    <div className="kb-grid">
      {companies.map((c) => (
        <div className="kb-co" key={c.company}>
          {c.company}
          <span className={`st ${c.status}`}>{ST_ZH[c.status]}</span>
        </div>
      ))}
    </div>
  )
}

/** 三灯分布统计（屏2复用） */
export function LampStats({ lamps }: { lamps: { r: number; y: number; g: number } }) {
  return (
    <>
      <span className="kb-stat">
        <b className="r">{lamps.r}</b>红灯
      </span>
      <span className="kb-stat">
        <b className="y">{lamps.y}</b>黄灯
      </span>
      <span className="kb-stat">
        <b className="g">{lamps.g}</b>绿灯
      </span>
    </>
  )
}

function Comp({ state }: ComponentProps<S>) {
  if (!state) return null
  const list = Object.entries(state.companies).map(([company, v]) => ({ company, status: v.status }))
  const done = list.filter((c) => c.status === 'reviewed').length
  const filled = list.filter((c) => c.status === 'filled').length
  const lamps = Object.values(state.companies).reduce(
    (acc, c) => ({ r: acc.r + c.lamp_r, y: acc.y + c.lamp_y, g: acc.g + c.lamp_g }),
    { r: 0, y: 0, g: 0 },
  )
  return (
    <div className="confirm-card">
      <div className="cc-hd">📊 风险填报批次看板 · {state.period}</div>
      <div className="kanban-stats" style={{ marginBottom: 10 }}>
        <span className="kb-stat">
          <b>{list.length}</b>企业
        </span>
        <span className="kb-stat">
          <b className="y">{filled}</b>已填待审
        </span>
        <span className="kb-stat">
          <b className="g">{done}</b>已审
        </span>
        <LampStats lamps={lamps} />
      </div>
      <KanbanGrid companies={list} />
    </div>
  )
}

const def: ComponentDefinition<S> = { kind: 'kanban-card', version: 1, match, reduce, component: Comp }
export default def
