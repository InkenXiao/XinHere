// lamp-adjust-panel@1：亮灯调整面板（hq 手工调灯 + 原因留痕）
// 事件族 kpi/lamp-adjust 经 aliases(company:indicator_name) 折进本基座，到达即 end 收尾
import { useState } from 'react'
import type { ComponentDefinition, ComponentProps } from '@/registry/types'
import type { PlatformEvent } from '@/types'
import type { Lamp } from '@contracts/events'
import { LampPick, LampPill } from '@/primitives/Lamp'

interface S {
  company: string
  indicator_name: string
  old_lamp: Lamp
  done?: boolean
  new_lamp?: Lamp
  reason?: string
}

function init(base: { props: Record<string, unknown> }): S {
  return {
    company: String(base.props.company ?? ''),
    indicator_name: String(base.props.indicator_name ?? ''),
    old_lamp: (base.props.old_lamp as Lamp) ?? 'y',
  }
}

function famId(d: Record<string, unknown>): string {
  return `${String(d.company ?? '')}:${String(d.indicator_name ?? '')}`
}

function match(evt: PlatformEvent) {
  if (evt.type === 'component/request' && evt.data.kind === 'lamp-adjust-panel') {
    return { id: String(evt.data.component_id), role: 'start' as const }
  }
  if (evt.type === 'kpi/lamp-adjust') {
    return { id: famId(evt.data), role: 'end' as const }
  }
  return null
}

function reduce(s: S | undefined, evt: PlatformEvent): S {
  if (!s) throw new Error('lamp-adjust-panel 状态缺失')
  if (evt.type === 'kpi/lamp-adjust') {
    return { ...s, done: true, new_lamp: evt.data.new_lamp as Lamp, reason: String(evt.data.reason ?? '') }
  }
  if (evt.type === 'component/submit') {
    return { ...s, done: true }
  }
  return s
}

function Comp({ base, state, emit }: ComponentProps<S>) {
  const s = state ?? init(base)
  const [newLamp, setNewLamp] = useState<Lamp>(s.old_lamp)
  const [reason, setReason] = useState('')
  const disabled = base.status !== 'open' || s.done === true
  const canSubmit = !disabled && newLamp !== s.old_lamp && reason.trim().length > 0
  return (
    <div className="confirm-card">
      <div className="cc-hd">💡 亮灯调整 · {s.company} · {s.indicator_name}</div>
      {disabled && s.done ? (
        <div className="modal-note">
          ✅ 已调整：{s.indicator_name} 由 <LampPill lamp={s.old_lamp} /> 调整为{' '}
          {s.new_lamp ? <LampPill lamp={s.new_lamp} /> : '（已取消）'}
          {s.reason ? ` · 原因：${s.reason}` : ''}
        </div>
      ) : (
        <>
          <div className="cc-row">
            <span>当前亮灯</span>
            <LampPill lamp={s.old_lamp} />
          </div>
          <div className="cc-row">
            <span>调整为</span>
            <LampPick value={newLamp} disabled={disabled} onChange={setNewLamp} />
          </div>
          <div className="cc-row" style={{ alignItems: 'flex-start' }}>
            <span>调整原因</span>
            <textarea
              className="fill-input"
              style={{ flex: 1 }}
              placeholder="必填，将留痕"
              value={reason}
              disabled={disabled}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 10 }}>
            <button className="btn-ghost" disabled={disabled} onClick={() => emit.submit('cancel')}>
              取消
            </button>
            <button
              className="btn-primary"
              disabled={!canSubmit}
              onClick={() => emit.submit('submit', { new_lamp: newLamp, reason: reason.trim() })}
            >
              确认调整
            </button>
          </div>
        </>
      )}
    </div>
  )
}

const def: ComponentDefinition<S> = {
  kind: 'lamp-adjust-panel',
  version: 1,
  match,
  init,
  aliases: (props) => [famId(props)],
  reduce,
  component: Comp,
}
export default def
