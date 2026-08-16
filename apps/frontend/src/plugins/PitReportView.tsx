// pit-report-view@1：投后报告生成进度（事件族直接起件，纯展示，不调 emit）
// 防乱序：首帧为 update 时以空骨架容错后再折
import type { ComponentDefinition, ComponentProps } from '@/registry/types'
import type { PlatformEvent } from '@/types'

interface S {
  report_id: string
  period: string
  company_ids: string[]
  outline: string[]
  sections: Record<number, string>
  summary?: string
  done?: boolean
}

function emptySkeleton(reportId: string): S {
  return { report_id: reportId, period: '', company_ids: [], outline: [], sections: {} }
}

function match(evt: PlatformEvent) {
  if (evt.type === 'pit/report-start') return { id: String(evt.data.report_id), role: 'start' as const }
  if (evt.type === 'pit/report-update') return { id: String(evt.data.report_id), role: 'update' as const }
  if (evt.type === 'pit/report-done') return { id: String(evt.data.report_id), role: 'end' as const }
  return null
}

function reduce(s: S | undefined, evt: PlatformEvent): S {
  if (evt.type === 'pit/report-start') {
    return {
      report_id: String(evt.data.report_id),
      period: String(evt.data.period ?? ''),
      company_ids: (evt.data.company_ids as string[]) ?? [],
      outline: (evt.data.outline as string[]) ?? [],
      sections: {},
    }
  }
  // 乱序容错：首帧非 start 时以空骨架再折
  const cur = s ?? emptySkeleton(String(evt.data.report_id ?? ''))
  if (evt.type === 'pit/report-update') {
    return { ...cur, sections: { ...cur.sections, [Number(evt.data.section_idx)]: String(evt.data.content ?? '') } }
  }
  if (evt.type === 'pit/report-done') {
    return { ...cur, done: true, summary: String(evt.data.summary ?? '') }
  }
  return cur
}

function Comp({ state }: ComponentProps<S>) {
  if (!state) return null
  return (
    <div className="confirm-card">
      <div className="cc-hd">
        📄 投后报告{state.period ? ` · ${state.period}` : ''}
        {state.company_ids.length > 0 ? ` · ${state.company_ids.length} 家` : ''}
      </div>
      <ul className="outline">
        {state.outline.map((t, i) => (
          <li key={i}>
            <span className="num">{String(i + 1).padStart(2, '0')}</span>
            <div style={{ flex: 1 }}>
              {t}
              {state.sections[i] !== undefined ? (
                <span className="sec-body">{state.sections[i]}</span>
              ) : (
                <span className="gen-hint">⏳ 生成中…</span>
              )}
            </div>
          </li>
        ))}
      </ul>
      {state.done && (
        <div className="modal-note" style={{ marginTop: 8 }}>
          ✅ 已入库{state.summary ? `：${state.summary}` : ''}
        </div>
      )}
    </div>
  )
}

const def: ComponentDefinition<S> = { kind: 'pit-report-view', version: 1, match, reduce, component: Comp }
export default def
