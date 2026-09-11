// Xin语页面雷达图：双层轨道 + 扫描扇区 + 十字准线 + 四象限待办信号（悬停出提示）
// 数据来自待办 store（登录后加载 + 30s 轮询 + SSE 刷新），按「重要×紧急」四象限分布
import { useMemo, useState } from 'react'
import { useTodoStore } from '@/state/todoStore'
import type { TodoItem } from '@/types'
import { SCENE_ZH, fmtTime } from '@/utils'

type Priority = 'important-urgent' | 'important-not-urgent' | 'urgent-not-important' | 'neither'

interface RadarDot {
  id: string
  priority: Priority
  title: string
  source: string
  time: string
  summary: string
  pos: [number, number] // left/top 百分比
}

const PRIORITY_META: Record<Priority, { label: string; rgb: string }> = {
  'important-urgent': { label: ' ', rgb: '255, 198, 112' },
  'important-not-urgent': { label: ' ', rgb: '150, 226, 255' },
  'urgent-not-important': { label: ' ', rgb: '118, 243, 210' },
  neither: { label: ' ', rgb: '168, 187, 205' },
}

/** 每象限最多展示的信号点（超出仅计数，避免拥挤） */
const MAX_DOTS_PER_QUADRANT = 3
/** 距截止不足该时限（或已逾期）视为紧急 */
const URGENT_WINDOW_MS = 48 * 3600_000

/** 紧急 = 48 小时内截止（含逾期）；重要 = 红黄灯风险或需人工审批/复核 */
function quadrantOf(t: TodoItem): Priority {
  const dueAt = t.due ? new Date(t.due).getTime() : null
  const urgent = dueAt !== null && dueAt - Date.now() < URGENT_WINDOW_MS
  const important = t.lamp === 'r' || t.lamp === 'y' || t.kind === 'review' || t.kind === 'feedback_review'
  if (important && urgent) return 'important-urgent'
  if (important) return 'important-not-urgent'
  if (urgent) return 'urgent-not-important'
  return 'neither'
}

/** 象限内信号点排序分：逾期 > 临期 > 灯色风险 */
function urgencyScore(t: TodoItem): number {
  const dueAt = t.due ? new Date(t.due).getTime() : Number.POSITIVE_INFINITY
  const overdue = dueAt < Date.now() ? 1000 : 0
  const soon = dueAt - Date.now() < URGENT_WINDOW_MS ? 400 : 0
  const lamp = t.lamp === 'r' ? 300 : t.lamp === 'y' ? 150 : 0
  return overdue + soon + lamp
}

function timeText(t: TodoItem): string {
  const dueAt = t.due ? new Date(t.due).getTime() : null
  if (dueAt !== null && dueAt < Date.now()) return `已逾期 · ${fmtTime(t.due)}`
  if (t.due) return `截止 ${fmtTime(t.due)}`
  return `创建于 ${fmtTime(t.created_at)}`
}

// 各象限散点的角度弧段（屏幕坐标，y 向下；-90° 为正上，0° 为正右）
const ARC: Record<Priority, [number, number]> = {
  'important-not-urgent': [-162, -108],
  'important-urgent': [-72, -18],
  'urgent-not-important': [18, 72],
  neither: [108, 162],
}

/** 在象限弧段上均匀布点，半径交替错开避免重叠 */
function posOf(p: Priority, idx: number, total: number): [number, number] {
  const [a0, a1] = ARC[p]
  const t = total <= 1 ? 0.5 : idx / (total - 1)
  const rad = ((a0 + (a1 - a0) * t) * Math.PI) / 180
  const r = 32 + (idx % 2) * 9
  return [50 + r * Math.cos(rad), 50 + r * Math.sin(rad)]
}

export default function RadarInstrument() {
  const items = useTodoStore((s) => s.items)
  const ignored = useTodoStore((s) => s.ignored)
  const [hover, setHover] = useState<RadarDot | null>(null)

  const { dots, counts, total, overdue } = useMemo(() => {
    const active = items.filter((t) => !ignored.has(t.todo_id) && t.status !== 'completed' && t.status !== 'na_closed')
    const groups: Record<Priority, TodoItem[]> = {
      'important-urgent': [],
      'important-not-urgent': [],
      'urgent-not-important': [],
      neither: [],
    }
    for (const t of active) groups[quadrantOf(t)].push(t)
    const counts: Record<Priority, number> = {
      'important-urgent': groups['important-urgent'].length,
      'important-not-urgent': groups['important-not-urgent'].length,
      'urgent-not-important': groups['urgent-not-important'].length,
      neither: groups.neither.length,
    }
    const dots: RadarDot[] = []
    for (const p of Object.keys(groups) as Priority[]) {
      const list = groups[p]
        .slice()
        .sort((a, b) => urgencyScore(b) - urgencyScore(a) || b.created_at.localeCompare(a.created_at))
        .slice(0, MAX_DOTS_PER_QUADRANT)
      list.forEach((t, i) => {
        dots.push({
          id: t.todo_id,
          priority: p,
          title: t.title,
          source: `${t.dispatcher_name} · ${SCENE_ZH[t.scene] ?? t.scene}`,
          time: timeText(t),
          summary: t.sub,
          pos: posOf(p, i, list.length),
        })
      })
    }
    const overdue = active.filter((t) => t.due && new Date(t.due).getTime() < Date.now()).length
    return { dots, counts, total: active.length, overdue }
  }, [items, ignored])

  return (
    <aside className="tw-radar" aria-label="待办优先级雷达">
      <div className="radar-orbit radar-orbit--outer" />
      <div className="radar-orbit radar-orbit--inner" />
      <div className="radar-sweep" />
      <div className="radar-crosshair" />
      <div className="radar-quadrants" aria-hidden="true">
        <span className="radar-q radar-q--inu">
          {PRIORITY_META['important-not-urgent'].label} <b>{String(counts['important-not-urgent']).padStart(2, '0')}</b>
        </span>
        <span className="radar-q radar-q--iu">
          {PRIORITY_META['important-urgent'].label} <b>{String(counts['important-urgent']).padStart(2, '0')}</b>
        </span>
        <span className="radar-q radar-q--nei">
          {PRIORITY_META.neither.label} <b>{String(counts.neither).padStart(2, '0')}</b>
        </span>
        <span className="radar-q radar-q--uni">
          {PRIORITY_META['urgent-not-important'].label} <b>{String(counts['urgent-not-important']).padStart(2, '0')}</b>
        </span>
      </div>
      <div className="radar-dots">
        {dots.map((d) => (
          <button
            type="button"
            key={d.id}
            className={`radar-dot radar-dot--${d.priority}`}
            style={{ left: `${d.pos[0]}%`, top: `${d.pos[1]}%` }}
            aria-label={`${PRIORITY_META[d.priority].label}：${d.title}`}
            onMouseEnter={() => setHover(d)}
            onMouseLeave={() => setHover(null)}
            onFocus={() => setHover(d)}
            onBlur={() => setHover(null)}
            tabIndex={-1}
          >
            <span className="radar-dot__core" />
          </button>
        ))}
      </div>
      {hover && (
        <div className="radar-tip" style={{ ['--tip-rgb' as string]: PRIORITY_META[hover.priority].rgb }}>
          <span style={{ color: `rgb(${PRIORITY_META[hover.priority].rgb})` }}>{PRIORITY_META[hover.priority].label}</span>
          <strong>{hover.title}</strong>
          <small>
            {hover.source} · {hover.time}
          </small>
          <p>{hover.summary}</p>
        </div>
      )}
      <div className="radar-signal">
        <span />
        <div>
          <strong>{total} 项待办</strong>
          <small>{overdue > 0 ? `逾期 ${overdue} 项` : '暂无逾期'}</small>
        </div>
      </div>
      <span className="radar-axis radar-axis--n"> </span>
      <span className="radar-axis radar-axis--e"> </span>
      <span className="radar-axis radar-axis--s"> </span>
      <span className="radar-axis radar-axis--w"> </span>
    </aside>
  )
}
