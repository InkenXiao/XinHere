// cash-guarantee-form@1：四字段试算（改值实时倍数 + 亮灯 ≤3红/≤6黄/>6绿）
// 金额单位：万元
import { useEffect, useState } from 'react'
import type { ComponentDefinition, ComponentProps } from '@/registry/types'
import type { PlatformEvent } from '@/types'
import type { CashValues } from '@contracts/events'
import { cashLamp, cashRatio } from '@/utils'
import { LampPill } from '@/primitives/Lamp'

interface S {
  form_id: string
  company: string
  period: string
  values: CashValues
  prev?: CashValues
  ratio: number
  lamp: 'r' | 'y' | 'g'
  summary?: string
  done?: boolean
}

function init(base: { props: Record<string, unknown> }): S {
  const values = (base.props.fields as CashValues) ?? {
    avail_cash: 0,
    pooled_fund: 0,
    avail_credit: 0,
    monthly_outflow: 0,
  }
  const ratio = cashRatio(values)
  return {
    form_id: String(base.props.form_id ?? ''),
    company: String(base.props.company ?? ''),
    period: String(base.props.period ?? ''),
    values,
    prev: base.props.prev as CashValues | undefined,
    ratio,
    lamp: cashLamp(ratio),
  }
}

function reduce(s: S | undefined, evt: PlatformEvent): S {
  if (!s) throw new Error('cash-guarantee-form 状态缺失')
  if (evt.type === 'cash/form-field-update') {
    const draft = evt.data.draft as CashValues
    const ratio = cashRatio(draft)
    return { ...s, values: draft, ratio, lamp: cashLamp(ratio) }
  }
  if (evt.type === 'cash/form-submit') {
    return {
      ...s,
      values: evt.data.values as CashValues,
      ratio: evt.data.ratio as number,
      lamp: evt.data.lamp as 'r' | 'y' | 'g',
      summary: String(evt.data.summary ?? ''),
      done: true,
    }
  }
  if (evt.type === 'component/submit') {
    return { ...s, summary: String(evt.data.summary ?? s.summary ?? ''), done: true }
  }
  return s
}

function match(evt: PlatformEvent) {
  if (evt.type === 'component/request' && evt.data.kind === 'cash-guarantee-form') {
    return { id: String(evt.data.component_id), role: 'start' as const }
  }
  if (evt.type === 'cash/form-field-update' || evt.type === 'cash/form-submit') {
    return { id: String(evt.data.form_id), role: evt.type === 'cash/form-submit' ? ('end' as const) : ('update' as const) }
  }
  return null
}

const FIELDS: { key: keyof CashValues; label: string; hint: string }[] = [
  { key: 'avail_cash', label: '可用货币资金', hint: '万元 · 扣除资金集中' },
  { key: 'pooled_fund', label: '资金集中金额', hint: '万元 · 可归集部分' },
  { key: 'avail_credit', label: '可用授信额度', hint: '万元 · 未使用授信' },
  { key: 'monthly_outflow', label: '月均付现支出', hint: '分母 · 用于算倍数' },
]

/** 展示与交互主体（会话组件与待办弹窗共用） */
export function CashGuaranteeView({
  company,
  period,
  initial,
  prev,
  disabled,
  onUpdate,
  onSubmit,
  onCancel,
  doneSummary,
}: {
  company: string
  period: string
  initial: CashValues
  prev?: CashValues
  disabled?: boolean
  onUpdate?: (v: CashValues) => Promise<void>
  onSubmit?: (v: CashValues) => Promise<void>
  onCancel?: () => Promise<void>
  doneSummary?: string
}) {
  const [v, setV] = useState<CashValues>(initial)
  const [busy, setBusy] = useState(false)
  useEffect(() => setV(initial), [JSON.stringify(initial)])
  const ratio = cashRatio(v)
  const lamp = cashLamp(ratio)
  const set = (k: keyof CashValues) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setV({ ...v, [k]: Number(e.target.value) || 0 })
  }
  const wrap = (fn?: (x: CashValues) => Promise<void>) => async () => {
    if (!fn) return
    setBusy(true)
    try {
      await fn(v)
    } finally {
      setBusy(false)
    }
  }
  if (doneSummary) {
    return <div className="modal-note">✅ {doneSummary}</div>
  }
  return (
    <div>
      <div className="modal-note">
        这是 <b>{company}</b> {period} 的「现金保障倍数」填报。上月数据已带出，确认或更新本月值即可，实时计算倍数和亮灯。单位：万元。
      </div>
      {FIELDS.map((f) => (
        <div className="frow" key={String(f.key)}>
          <div className="lbl">
            {f.label}
            <small>{f.hint}</small>
            {prev && <div className="prev">上月：{prev[f.key].toLocaleString()}</div>}
          </div>
          <input type="number" value={v[f.key] || ''} onChange={set(f.key)} disabled={disabled || busy} placeholder="0" />
        </div>
      ))}
      <div className="calc">
        <span className="ratio">
          现金保障倍数<b>{ratio.toFixed(2)}</b>
        </span>
        <LampPill lamp={lamp} />
      </div>
      {ratio <= 6 && <div className="warn">⚠ 现金保障倍数 {ratio <= 3 ? '≤ 3，已触发红灯预警' : '≤ 6，黄灯关注'}，请确认本月数据无误</div>}
      <div className="rule-pop">
        告警规则：倍数 ≤ 3 亮<span>红灯</span> · ≤ 6 亮<span>黄灯</span> · ＞6 亮绿灯
      </div>
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 14 }}>
        {onCancel && (
          <button className="btn-ghost" disabled={disabled || busy} onClick={wrap(onCancel)}>
            取消
          </button>
        )}
        {onUpdate && (
          <button className="btn-ghost" disabled={disabled || busy} onClick={wrap(onUpdate)}>
            保存草稿
          </button>
        )}
        {onSubmit && (
          <button className="btn-primary" disabled={disabled || busy || v.monthly_outflow <= 0} onClick={wrap(onSubmit)}>
            提交
          </button>
        )}
      </div>
    </div>
  )
}

function Comp({ base, state, emit }: ComponentProps<S>) {
  const s = state ?? init(base)
  return (
    <div className="confirm-card">
      <div className="cc-hd">💰 现金保障倍数试算 · {s.company}</div>
      <CashGuaranteeView
        company={s.company}
        period={s.period}
        initial={s.values}
        prev={s.prev}
        disabled={base.status !== 'open'}
        doneSummary={s.done ? s.summary ?? '已提交' : undefined}
        onUpdate={async (v) => emit.update({ draft: v })}
        onSubmit={async (v) => emit.submit('submit', { values: v })}
        onCancel={async () => emit.submit('cancel')}
      />
    </div>
  )
}

const def: ComponentDefinition<S> = {
  kind: 'cash-guarantee-form',
  version: 1,
  match,
  init,
  aliases: (props) => (props.form_id ? [String(props.form_id)] : []),
  reduce,
  component: Comp,
}
export default def
