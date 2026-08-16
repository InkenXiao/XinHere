// Toast：per-show key remount（每条新 toast 重新挂载，动画重放）
import { useUiStore } from '@/state/uiStore'

export default function ToastHost() {
  const toasts = useUiStore((s) => s.toasts)
  if (toasts.length === 0) return null
  return (
    <div className="toast-host">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind === 'err' ? 'err' : ''}`}>
          {t.text}
        </div>
      ))}
    </div>
  )
}
