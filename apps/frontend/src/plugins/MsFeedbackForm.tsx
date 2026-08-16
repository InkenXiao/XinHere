// ms-feedback-form@1：里程碑反馈 + 亮灯
import { useEffect, useState } from 'react'
import type { ComponentDefinition, ComponentProps } from '@/registry/types'
import type { MsFeedback, PlatformEvent } from '@/types'
import type { Lamp } from '@contracts/events'
import { api } from '@/transport/api'
import { LampPick } from '@/primitives/Lamp'
import { useUiStore } from '@/state/uiStore'

interface S {
  feedback_id: string
  company: string
  milestone_content: string
  status: string
  progress: number
  actual_date?: string
  status_note?: string
  lamp: Lamp
  done?: boolean
}

function init(base: { props: Record<string, unknown> }): S {
  return {
    feedback_id: String(base.props.feedback_id ?? ''),
    company: String(base.props.company ?? ''),
    milestone_content: String(base.props.milestone_content ?? ''),
    status: String(base.props.status ?? '进行中'),
    progress: Number(base.props.progress ?? 0),
    actual_date: base.props.actual_date as string | undefined,
    status_note: base.props.status_note as string | undefined,
    lamp: (base.props.lamp as Lamp) ?? 'g',
  }
}

function match(evt: PlatformEvent) {
  if (evt.type === 'component/request' && evt.data.kind === 'ms-feedback-form') {
    return { id: String(evt.data.component_id), role: 'start' as const }
  }
  if (evt.type === 'kpi/ms-feedback') {
    return { id: String(evt.data.feedback_id), role: 'update' as const }
  }
  return null
}

function reduce(s: S | undefined, evt: PlatformEvent): S {
  if (!s) throw new Error('ms-feedback-form 状态缺失')
  if (evt.type === 'kpi/ms-feedback') {
    return {
      ...s,
      status: String(evt.data.status ?? s.status),
      progress: Number(evt.data.progress ?? s.progress),
      actual_date: evt.data.actual_date as string | undefined,
      status_note: evt.data.status_note as string | undefined,
      lamp: evt.data.lamp as Lamp,
    }
  }
  return s
}

const STATUS_OPTIONS = ['未开始', '进行中', '已完成', '滞后']

export function MsFeedbackView({
  feedbackId,
  fallback,
  onDone,
}: {
  feedbackId: string
  fallback?: Partial<MsFeedback>
  onDone?: () => void
}) {
  const toast = useUiStore((s) => s.toast)
  const [fb, setFb] = useState<Partial<MsFeedback>>(fallback ?? {})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (fallback?.milestone_content) return
    let live = true
    const company = fallback?.company
    api<{ items?: MsFeedback[] } | MsFeedback[]>('GET', `/kpi/ms-feedbacks${company ? `?company=${encodeURIComponent(company)}` : ''}`)
      .then((r) => {
        if (!live) return
        const list = Array.isArray(r) ? r : (r.items ?? [])
        const found = list.find((x) => x.feedback_id === feedbackId)
        if (found) setFb(found)
        else setErr('未找到该里程碑反馈')
      })
      .catch((e) => setErr((e as Error).message))
    return () => {
      live = false
    }
  }, [feedbackId]) // eslint-disable-line react-hooks/exhaustive-deps

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
  const payload = () => ({
    status: fb.status ?? '进行中',
    progress: Number(fb.progress ?? 0),
    actual_date: fb.actual_date,
    lamp: fb.lamp ?? 'g',
    status_note: fb.status_note,
  })
  const save = wrap(async () => {
    await api('PUT', `/kpi/ms-feedbacks/${feedbackId}`, payload())
    toast('已保存')
  })
  const submit = wrap(async () => {
    await api('PUT', `/kpi/ms-feedbacks/${feedbackId}`, payload())
    await api('POST', `/kpi/ms-feedbacks/${feedbackId}/submit`)
    toast('已提交反馈')
    onDone?.()
  })

  if (err) return <div className="err-card">加载失败：{err}</div>

  return (
    <div>
      <div className="modal-note">
        里程碑：<b>{fb.milestone_content ?? '…'}</b>
      </div>
      <div className="form-row">
        <label>
          里程碑状态<span className="req">*</span>
        </label>
        <select
          className="fill-input"
          value={fb.status ?? '进行中'}
          onChange={(e) => setFb({ ...fb, status: e.target.value })}
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </div>
      <div className="form-row">
        <label>
          实际里程碑进度（%）<span className="req">*</span>
        </label>
        <input
          className="fill-input"
          style={{ width: 120 }}
          type="number"
          min={0}
          max={100}
          value={fb.progress ?? 0}
          onChange={(e) => setFb({ ...fb, progress: Number(e.target.value) || 0 })}
        />
      </div>
      <div className="form-row">
        <label>实际完成日期</label>
        <input
          className="fill-input"
          style={{ width: 180 }}
          type="date"
          value={fb.actual_date ?? ''}
          onChange={(e) => setFb({ ...fb, actual_date: e.target.value })}
        />
      </div>
      <div className="form-row">
        <label>
          亮灯<span className="req">*</span>
        </label>
        <LampPick value={fb.lamp ?? 'g'} onChange={(l) => setFb({ ...fb, lamp: l })} />
      </div>
      <div className="form-row">
        <label>进度备注</label>
        <textarea
          className="fill-input"
          value={fb.status_note ?? ''}
          onChange={(e) => setFb({ ...fb, status_note: e.target.value })}
        />
      </div>
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 10 }}>
        <button className="btn-ghost" disabled={busy} onClick={save}>
          保存
        </button>
        <button className="btn-primary" disabled={busy} onClick={submit}>
          提交反馈
        </button>
      </div>
    </div>
  )
}

function Comp({ base, state }: ComponentProps<S>) {
  const s = state ?? init(base)
  return (
    <div className="confirm-card">
      <div className="cc-hd">🚩 里程碑反馈 · {s.company}</div>
      <MsFeedbackView
        feedbackId={s.feedback_id}
        fallback={{
          feedback_id: s.feedback_id,
          company: s.company,
          milestone_content: s.milestone_content,
          status: s.status,
          progress: s.progress,
          actual_date: s.actual_date,
          status_note: s.status_note,
          lamp: s.lamp,
        }}
      />
    </div>
  )
}

const def: ComponentDefinition<S> = {
  kind: 'ms-feedback-form',
  version: 1,
  match,
  init,
  aliases: (props) => (props.feedback_id ? [String(props.feedback_id)] : []),
  reduce,
  component: Comp,
}
export default def
