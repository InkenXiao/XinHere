// 红黄绿灯点 / 灯牌
import type { Lamp } from '@contracts/events'

const ZH: Record<Lamp, string> = { r: '红灯', y: '黄灯', g: '绿灯' }

export function LampDot({ lamp, title }: { lamp: Lamp | null | undefined; title?: string }) {
  if (!lamp) return <span className="lamp-dot" style={{ background: 'var(--ink-30)', boxShadow: 'none' }} title={title} />
  return <span className={`lamp-dot ${lamp}`} title={title ?? ZH[lamp]} />
}

export function LampPill({ lamp }: { lamp: Lamp }) {
  return (
    <span className={`lamp-pill ${lamp}`}>
      <i />
      {ZH[lamp]}
    </span>
  )
}

/** 三灯选择器 */
export function LampPick({ value, onChange, disabled }: { value: Lamp; onChange?: (l: Lamp) => void; disabled?: boolean }) {
  const lamps: Lamp[] = ['r', 'y', 'g']
  return (
    <span className="lamp-pick">
      {lamps.map((l) => (
        <button
          key={l}
          type="button"
          className={value === l ? 'on' : ''}
          disabled={disabled}
          title={ZH[l]}
          onClick={() => onChange?.(l)}
        >
          <i className={`lamp-dot ${l}`} />
        </button>
      ))}
    </span>
  )
}
