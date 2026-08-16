// kpi-fill-form@1：经营者考核指标 + 里程碑拆分
import { useEffect, useState } from 'react'
import type { ComponentDefinition, ComponentProps } from '@/registry/types'
import type { KpiIndicator, KpiMilestone, PlatformEvent } from '@/types'
import { api } from '@/transport/api'
import { useUiStore } from '@/state/uiStore'

interface S {
  batch_id: string
  company: string
  done?: boolean
}

function init(base: { props: Record<string, unknown> }): S {
  return { batch_id: String(base.props.batch_id ?? ''), company: String(base.props.company ?? '') }
}

function famId(d: Record<string, unknown>): string {
  return `${String(d.batch_id ?? '')}:${String(d.company ?? '')}`
}

function match(evt: PlatformEvent) {
  if (evt.type === 'component/request' && evt.data.kind === 'kpi-fill-form') {
    return { id: String(evt.data.component_id), role: 'start' as const }
  }
  if (evt.type === 'kpi/indicator-update' || evt.type === 'kpi/ms-split' || evt.type === 'kpi/review') {
    return { id: famId(evt.data), role: 'update' as const }
  }
  return null
}

function reduce(s: S | undefined, evt: PlatformEvent): S {
  if (!s) throw new Error('kpi-fill-form 状态缺失')
  if (evt.type === 'kpi/review') return { ...s, done: true }
  return s
}

export function KpiFillView({ batchId, company, onDone }: { batchId: string; company: string; onDone?: () => void }) {
  const toast = useUiStore((s) => s.toast)
  const [indicators, setIndicators] = useState<KpiIndicator[]>([])
  const [milestones, setMilestones] = useState<KpiMilestone[]>([])
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    api<{ indicators: KpiIndicator[]; milestones: KpiMilestone[] }>(
      'GET',
      `/kpi/batches/${batchId}/companies/${company}`,
    )
      .then((r) => {
        if (!live) return
        setIndicators(r.indicators ?? [])
        setMilestones(r.milestones ?? [])
        setLoaded(true)
      })
      .catch((e) => setErr((e as Error).message))
    return () => {
      live = false
    }
  }, [batchId, company])

  const setInd = (id: string, patch: Partial<KpiIndicator>) =>
    setIndicators((arr) => arr.map((x) => (x.indicator_id === id ? { ...x, ...patch } : x)))
  const setMs = (i: number, patch: Partial<KpiMilestone>) =>
    setMilestones((arr) => arr.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  const addMs = (indicatorId: string) =>
    setMilestones((arr) => [...arr, { indicator_id: indicatorId, content: '', plan_date: '', material: '' }])
  const delMs = (i: number) => setMilestones((arr) => arr.filter((_, j) => j !== i))

  const wrap = (fn: () => Promise<void>) => async () => {
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      toast((e as Error).message, 'err')
    } finally {
      setBusy(false)
    }
  }
  const save = wrap(async () => {
    await api('PUT', `/kpi/batches/${batchId}/companies/${company}/indicators`, {
      indicators: indicators.map((x) => ({
        indicator_id: x.indicator_id,
        content: x.content,
        base_score: x.base_score,
        max_score: x.max_score,
      })),
    })
    await api('PUT', `/kpi/batches/${batchId}/companies/${company}/milestones`, {
      milestones: milestones.map((x) => ({
        indicator_id: x.indicator_id,
        content: x.content,
        plan_date: x.plan_date,
        material: x.material,
      })),
    })
    toast('草稿已保存')
  })
  const submit = wrap(async () => {
    await api('PUT', `/kpi/batches/${batchId}/companies/${company}/indicators`, {
      indicators: indicators.map((x) => ({
        indicator_id: x.indicator_id,
        content: x.content,
        base_score: x.base_score,
        max_score: x.max_score,
      })),
    })
    await api('PUT', `/kpi/batches/${batchId}/companies/${company}/milestones`, {
      milestones: milestones.map((x) => ({
        indicator_id: x.indicator_id,
        content: x.content,
        plan_date: x.plan_date,
        material: x.material,
      })),
    })
    await api('POST', `/kpi/batches/${batchId}/companies/${company}/submit`)
    toast('已提交审批')
    onDone?.()
  })

  if (err) return <div className="err-card">加载失败：{err}</div>
  if (!loaded) return <div className="modal-note">加载中…</div>

  return (
    <div>
      <div className="modal-note">
        <b>{company}</b> 经营者考核：先填指标内容/分值，再拆分里程碑。
      </div>
      <table className="kpi-table">
        <thead>
          <tr>
            <th>维度</th>
            <th>指标</th>
            <th>类型</th>
            <th>考核内容</th>
            <th>基础分</th>
            <th>满分</th>
          </tr>
        </thead>
        <tbody>
          {indicators.map((x) => (
            <tr key={x.indicator_id}>
              <td>{x.dim}</td>
              <td>{x.name}</td>
              <td>{x.kpi_type}</td>
              <td>
                <textarea className="fill-input" value={x.content} onChange={(e) => setInd(x.indicator_id, { content: e.target.value })} />
              </td>
              <td>
                <input className="fill-input" value={x.base_score} onChange={(e) => setInd(x.indicator_id, { base_score: e.target.value })} />
              </td>
              <td>
                <input className="fill-input" value={x.max_score} onChange={(e) => setInd(x.indicator_id, { max_score: e.target.value })} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ margin: '14px 0 8px', fontSize: 13, fontWeight: 600, color: 'var(--ink-100)' }}>里程碑拆分</div>
      <table className="kpi-table">
        <thead>
          <tr>
            <th>关联指标</th>
            <th>里程碑内容</th>
            <th>计划日期</th>
            <th>交付材料</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {milestones.map((m, i) => (
            <tr key={i}>
              <td>
                <select className="fill-input" value={m.indicator_id} onChange={(e) => setMs(i, { indicator_id: e.target.value })}>
                  {indicators.map((x) => (
                    <option key={x.indicator_id} value={x.indicator_id}>
                      {x.name}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <input className="fill-input" value={m.content} onChange={(e) => setMs(i, { content: e.target.value })} />
              </td>
              <td>
                <input className="fill-input" type="date" value={m.plan_date} onChange={(e) => setMs(i, { plan_date: e.target.value })} />
              </td>
              <td>
                <input className="fill-input" value={m.material} onChange={(e) => setMs(i, { material: e.target.value })} />
              </td>
              <td>
                <button className="btn-ghost" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => delMs(i)}>
                  删
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button
        className="btn-ghost"
        style={{ marginTop: 8, fontSize: 12 }}
        onClick={() => addMs(indicators[0]?.indicator_id ?? '')}
        disabled={indicators.length === 0}
      >
        + 添加里程碑
      </button>

      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 14 }}>
        <button className="btn-ghost" disabled={busy} onClick={save}>
          保存草稿
        </button>
        <button className="btn-primary" disabled={busy} onClick={submit}>
          提交审批
        </button>
      </div>
    </div>
  )
}

function Comp({ base, state }: ComponentProps<S>) {
  const s = state ?? init(base)
  return (
    <div className="confirm-card">
      <div className="cc-hd">📑 经营者考核填报 · {s.company}</div>
      <KpiFillView batchId={s.batch_id} company={s.company} />
    </div>
  )
}

const def: ComponentDefinition<S> = {
  kind: 'kpi-fill-form',
  version: 1,
  match,
  init,
  aliases: (props) => [`${String(props.batch_id ?? '')}:${String(props.company ?? '')}`],
  reduce,
  component: Comp,
}
export default def
