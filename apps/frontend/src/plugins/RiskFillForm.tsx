// risk-fill-form@1：16 项填报（红黄绿灯选择+字段组；pf=true 预算值只读预填；草稿 update + submit）
// 明细数据走 REST（/risk-fills，页面与工具共用 service 层），事件族折叠头部状态
import { useEffect, useMemo, useState } from 'react'
import type { ComponentDefinition, ComponentProps } from '@/registry/types'
import type { PlatformEvent, RiskItem, RiskReport } from '@/types'
import { api } from '@/transport/api'
import { LampPick } from '@/primitives/Lamp'
import { useUiStore } from '@/state/uiStore'

interface S {
  batch_id: string
  company: string
  mode: 'fill' | 'review'
  status?: string
  lamp_r?: number
  lamp_y?: number
  lamp_g?: number
  summary?: string
}

function init(base: { props: Record<string, unknown> }): S {
  return {
    batch_id: String(base.props.batch_id ?? ''),
    company: String(base.props.company ?? ''),
    mode: base.props.mode === 'review' ? 'review' : 'fill',
  }
}

function famId(d: Record<string, unknown>): string {
  return `${String(d.batch_id ?? '')}:${String(d.company ?? '')}`
}

function match(evt: PlatformEvent) {
  if (evt.type === 'component/request' && evt.data.kind === 'risk-fill-form') {
    return { id: String(evt.data.component_id), role: 'start' as const }
  }
  if (evt.type === 'risk/report-update' || evt.type === 'risk/submit') {
    return { id: famId(evt.data), role: 'update' as const }
  }
  if (evt.type === 'risk/review') {
    return { id: famId(evt.data), role: 'update' as const }
  }
  return null
}

function reduce(s: S | undefined, evt: PlatformEvent): S {
  if (!s) throw new Error('risk-fill-form 状态缺失')
  if (evt.type === 'risk/report-update') {
    return {
      ...s,
      status: String(evt.data.status ?? s.status ?? ''),
      lamp_r: evt.data.lamp_r as number,
      lamp_y: evt.data.lamp_y as number,
      lamp_g: evt.data.lamp_g as number,
    }
  }
  if (evt.type === 'risk/submit') {
    return { ...s, status: 'filled', summary: String(evt.data.summary ?? '') }
  }
  if (evt.type === 'risk/review') {
    return { ...s, status: evt.data.approve ? 'reviewed' : 'unfilled' }
  }
  return s
}

/** 16 项填报主体（会话组件与待办弹窗共用） */
export function RiskFillView({
  batchId,
  company,
  mode,
  onDone,
}: {
  batchId: string
  company: string
  mode: 'fill' | 'review'
  onDone?: () => void
}) {
  const toast = useUiStore((s) => s.toast)
  const [report, setReport] = useState<RiskReport | null>(null)
  const [items, setItems] = useState<RiskItem[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    api<RiskReport>('GET', `/risk-fills/${batchId}/reports/${company}`)
      .then((r) => {
        if (!live) return
        setReport(r)
        setItems(r.items ?? [])
      })
      .catch((e) => setErr((e as Error).message))
    return () => {
      live = false
    }
  }, [batchId, company])

  const counts = useMemo(() => {
    const c = { r: 0, y: 0, g: 0 }
    for (const it of items) c[it.lamp]++
    return c
  }, [items])

  const readonly = report?.status === 'filled' || report?.status === 'reviewed' || mode === 'review'

  const setLamp = (idx: number, lamp: 'r' | 'y' | 'g') => {
    setItems((arr) => arr.map((it) => (it.idx === idx ? { ...it, lamp } : it)))
  }
  const setVal = (idx: number, fi: number, val: string) => {
    setItems((arr) =>
      arr.map((it) =>
        it.idx === idx ? { ...it, fields: it.fields.map((f, i) => (i === fi ? { ...f, v: val } : f)) } : it,
      ),
    )
  }

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

  const saveDraft = wrap(async () => {
    await api('PUT', `/risk-fills/${batchId}/reports/${company}/items`, { items })
    toast('草稿已保存')
  })
  const submit = wrap(async () => {
    await api('PUT', `/risk-fills/${batchId}/reports/${company}/items`, { items })
    await api('POST', `/risk-fills/${batchId}/reports/${company}/submit`)
    toast('已提交审核')
    onDone?.()
  })
  const review = (approve: boolean) =>
    wrap(async () => {
      await api('POST', `/risk-fills/${batchId}/reports/${company}/review`, { approve })
      toast(approve ? '已通过审批' : '已驳回')
      onDone?.()
    })

  if (err) return <div className="err-card">加载失败：{err}</div>
  if (!report) return <div className="modal-note">加载中…</div>

  return (
    <div>
      <div className="modal-note">
        <b>{company}</b> 共 <b>{items.length}</b> 项风险预警财务指标（预算值字段系统预填只读，单位：万元）。
        当前：🔴 {counts.r} · 🟡 {counts.y} · 🟢 {counts.g}
        {report.status !== 'unfilled' && ` · 状态：${report.status === 'filled' ? '已提交待审' : '已审批'}`}
      </div>
      {items.map((it) => (
        <div className="fill-cat" key={it.idx}>
          <div className="fill-cat-hd">
            <span className="idx">{String(it.idx).padStart(2, '0')}</span>
            {it.name}
            <LampPick value={it.lamp} disabled={readonly} onChange={(l) => setLamp(it.idx, l)} />
          </div>
          {it.fields.map((f, fi) => (
            <div className="fill-field" key={fi}>
              <span className="ff-name">
                {f.k}
                {f.pf && <span className="pf-tag">预填</span>}
              </span>
              <input
                className="fill-input"
                value={f.v}
                readOnly={!!f.pf || readonly}
                onChange={(e) => setVal(it.idx, fi, e.target.value)}
              />
            </div>
          ))}
        </div>
      ))}
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 10 }}>
        {mode === 'fill' && !readonly && (
          <>
            <button className="btn-ghost" disabled={busy} onClick={saveDraft}>
              保存草稿
            </button>
            <button className="btn-primary" disabled={busy} onClick={submit}>
              提交审核
            </button>
          </>
        )}
        {mode === 'review' && report.status === 'filled' && (
          <>
            <button className="btn-ghost" disabled={busy} onClick={review(false)}>
              驳回
            </button>
            <button className="btn-primary" disabled={busy} onClick={review(true)}>
              确认通过
            </button>
          </>
        )}
      </div>
    </div>
  )
}

function Comp({ base, state }: ComponentProps<S>) {
  const s = state ?? init(base)
  return (
    <div className="confirm-card">
      <div className="cc-hd">📝 风险预警财务指标填报 · {s.company}</div>
      <RiskFillView batchId={s.batch_id} company={s.company} mode={s.mode} />
    </div>
  )
}

const def: ComponentDefinition<S> = {
  kind: 'risk-fill-form',
  version: 1,
  match,
  init,
  aliases: (props) => [`${String(props.batch_id ?? '')}:${String(props.company ?? '')}`],
  reduce,
  component: Comp,
}
export default def
