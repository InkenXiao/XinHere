// report-confirm@1：报告生成确认卡（选择时间[年/月] + 选择模板 + 被投企业多选[投后] + 确认/取消）
// 选项全部由后端 props 填充（模型只选不填）；values 随 submit 带回（含 report_id 供 resume 幂等）
import { useState } from 'react'
import type { ComponentDefinition, ComponentProps } from '@/registry/types'
import type { PlatformEvent } from '@/types'

interface Tpl { key: string; name: string }

interface S {
  title: string
  skillKey: string
  defaultPeriod: string // YYYY-MM
  yearOptions: number[]
  monthOptions: number[]
  templates: Tpl[]
  companies: string[] // 空数组 = 不展示企业选择（财务风险报告）
  draftCompanies: string[]
  reportId: string
  status: 'open' | 'submitted' | 'cancelled'
}

function init(base: { props: Record<string, unknown> }): S {
  const p = base.props
  return {
    title: String(p.title ?? '请确认以下信息'),
    skillKey: String(p.skill_key ?? ''),
    defaultPeriod: String(p.default_period ?? ''),
    yearOptions: (p.year_options as number[]) ?? [],
    monthOptions: (p.month_options as number[]) ?? [],
    templates: (p.templates as Tpl[]) ?? [],
    companies: (p.companies as string[]) ?? [],
    draftCompanies: (p.draft_company_ids as string[]) ?? (p.companies as string[]) ?? [],
    reportId: String(p.report_id ?? ''),
    status: 'open',
  }
}

function match(evt: PlatformEvent) {
  if (evt.type === 'component/request' && evt.data.kind === 'report-confirm') {
    return { id: String(evt.data.component_id), role: 'start' as const }
  }
  return null
}

function reduce(s: S | undefined, evt: PlatformEvent): S {
  if (!s) throw new Error('report-confirm 状态缺失')
  if (evt.type === 'component/submit') {
    return { ...s, status: (evt.data.action as string) === 'cancelled' ? 'cancelled' : 'submitted' }
  }
  return s
}

function Comp({ base, state, emit }: ComponentProps<S>) {
  const s = state ?? init(base)
  const [dY, dM] = s.defaultPeriod.split('-')
  const [year, setYear] = useState(Number(dY) || s.yearOptions[s.yearOptions.length - 1])
  const [month, setMonth] = useState(Number(dM) || new Date().getMonth() + 1)
  const [tpl, setTpl] = useState(s.templates[0]?.key ?? '')
  const [sel, setSel] = useState<Set<string>>(new Set(s.draftCompanies))
  const disabled = s.status !== 'open'

  const toggleCo = (c: string) => {
    const n = new Set(sel)
    if (n.has(c)) n.delete(c)
    else n.add(c)
    setSel(n)
  }

  const submit = () => {
    const period = `${year}-${String(month).padStart(2, '0')}`
    emit.submit('submit', {
      period,
      template_key: tpl,
      company_ids: [...sel],
      report_id: s.reportId,
    })
  }

  return (
    <div className="rc-card">
      <div className="rc-title">{s.title}</div>
      <div className="rc-field">
        <div className="rc-label">选择时间</div>
        <div className="rc-time-row">
          <select className="rc-select" value={year} disabled={disabled}
            onChange={(e) => setYear(Number(e.target.value))}>
            {s.yearOptions.map((y) => (
              <option key={y} value={y}>{y}年</option>
            ))}
          </select>
          <select className="rc-select" value={month} disabled={disabled}
            onChange={(e) => setMonth(Number(e.target.value))}>
            {s.monthOptions.map((m) => (
              <option key={m} value={m}>{m}月</option>
            ))}
          </select>
        </div>
      </div>
      <div className="rc-field">
        <div className="rc-label">选择模板</div>
        <select className="rc-select rc-select-full" value={tpl} disabled={disabled}
          onChange={(e) => setTpl(e.target.value)}>
          {s.templates.map((t) => (
            <option key={t.key} value={t.key}>{t.name}</option>
          ))}
        </select>
      </div>
      {s.companies.length > 0 && (
        <div className="rc-field">
          <div className="rc-label">选择被投企业</div>
          <div className="rc-chips">
            {s.companies.map((c) => (
              <button
                key={c}
                className={`rc-chip ${sel.has(c) ? 'on' : ''}`}
                disabled={disabled}
                onClick={() => toggleCo(c)}
              >
                {sel.has(c) && <span className="rc-tick">✓</span>}
                {c}
              </button>
            ))}
          </div>
        </div>
      )}
      <button
        className="rc-confirm"
        disabled={disabled || (s.companies.length > 0 && sel.size === 0)}
        onClick={submit}
      >
        {s.status === 'submitted' ? '已确认' : '确认'}
      </button>
      {s.status === 'open' && (
        <button className="rc-cancel" onClick={() => emit.submit('cancel')}>
          取消
        </button>
      )}
    </div>
  )
}

const def: ComponentDefinition<S> = { kind: 'report-confirm', version: 1, match, init, reduce, component: Comp }
export default def
