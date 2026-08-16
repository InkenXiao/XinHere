// risk-dispatch-confirm@1：发起确认卡（公司清单+期间+确认/取消）
import { useState } from 'react'
import type { ComponentDefinition, ComponentProps } from '@/registry/types'
import type { PlatformEvent } from '@/types'

interface S {
  period: string
  companies: string[]
  selected: Set<string>
  status: 'open' | 'submitted' | 'cancelled'
}

function init(base: { props: Record<string, unknown> }): S {
  return {
    period: String(base.props.period ?? ''),
    companies: (base.props.companies as string[]) ?? [],
    selected: new Set((base.props.companies as string[]) ?? []),
    status: 'open',
  }
}

function reduce(s: S | undefined, evt: PlatformEvent): S {
  if (!s) throw new Error('risk-dispatch-confirm 状态缺失')
  if (evt.type === 'component/submit') {
    return { ...s, status: (evt.data.action as string) === 'cancelled' ? 'cancelled' : 'submitted' }
  }
  return s
}

function match(evt: PlatformEvent) {
  // component/submit 由 Assembler 原生折叠（base.status + reduce end 角色）
  if (evt.type === 'component/request' && evt.data.kind === 'risk-dispatch-confirm') {
    return { id: String(evt.data.component_id), role: 'start' as const }
  }
  return null
}

function Comp({ base, state, emit }: ComponentProps<S>) {
  const s = state ?? init(base)
  const [period, setPeriod] = useState(s.period)
  const [sel, setSel] = useState<Set<string>>(new Set(s.selected))
  const all = s.companies.length > 0 && sel.size === s.companies.length
  const toggleAll = () => setSel(all ? new Set() : new Set(s.companies))
  const toggleOne = (c: string) => {
    const n = new Set(sel)
    if (n.has(c)) n.delete(c)
    else n.add(c)
    setSel(n)
  }
  const disabled = s.status !== 'open'
  return (
    <div className="confirm-card">
      <div className="cc-hd">📋 风险预警财务指标填报 · 发起确认</div>
      <div className="cc-row">
        <span>归属期间</span>
        <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} disabled={disabled} className="fill-input" />
      </div>
      <div className="cc-row">
        <span>被投企业（共 {s.companies.length} 家）</span>
        <button className="btn-ghost" style={{ padding: '3px 10px', fontSize: 11 }} onClick={toggleAll} disabled={disabled}>
          {all ? '取消全选' : '全选'}
        </button>
      </div>
      <div className="cc-list">
        {s.companies.map((c) => (
          <label key={c} className="cc-co">
            <input type="checkbox" checked={sel.has(c)} onChange={() => toggleOne(c)} disabled={disabled} />
            {c}
          </label>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 10, marginTop: 10 }}>
        <button className="btn-ghost" disabled={disabled} onClick={() => emit.submit('cancel')}>
          取消
        </button>
        <button className="btn-primary" disabled={disabled || sel.size === 0} onClick={() => emit.submit('submit', { period, companies: [...sel] })}>
          确认下发 {sel.size} 家
        </button>
      </div>
    </div>
  )
}

const def: ComponentDefinition<S> = { kind: 'risk-dispatch-confirm', version: 1, match, init, reduce, component: Comp }
export default def
