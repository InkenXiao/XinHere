// 历史会话栏：搜索过滤 + 日期分组（今天/昨天/近7天/更早，组可折叠）+ pending 红点
import { useMemo, useState } from 'react'
import { useSessionStore } from '@/state/sessionStore'
import type { SessionListItem } from '@/types'
import { dayGroup, fmtTime } from '@/utils'

const GROUP_ORDER = ['今天', '昨天', '近 7 天', '更早']

export default function HistoryRail({ onCollapse }: { onCollapse?: () => void }) {
  const sessions = useSessionStore((s) => s.sessions)
  const current = useSessionStore((s) => s.current)
  const openSession = useSessionStore((s) => s.openSession)
  const newSession = useSessionStore((s) => s.newSession)
  const [kw, setKw] = useState('')
  const [closedGroups, setClosedGroups] = useState<Set<string>>(new Set())

  const groups = useMemo(() => {
    const k = kw.trim()
    const filtered = k
      ? sessions.filter((s) => (s.title ?? '').includes(k) || (s.last_message ?? '').includes(k))
      : sessions
    const map = new Map<string, SessionListItem[]>()
    for (const g of GROUP_ORDER) map.set(g, [])
    for (const s of filtered) map.get(dayGroup(s.updated_at))?.push(s)
    return GROUP_ORDER.map((g) => ({ name: g, items: map.get(g) ?? [] })).filter((g) => g.items.length > 0)
  }, [sessions, kw])

  const toggleGroup = (g: string) => {
    const n = new Set(closedGroups)
    if (n.has(g)) n.delete(g)
    else n.add(g)
    setClosedGroups(n)
  }

  return (
    <>
      <div className="col-head">
        <div className="t">
          <span className="h-ico">🕘</span>历史会话
        </div>
        <button className="icon-btn" onClick={onCollapse} title="收起">
          ‹
        </button>
      </div>
      <button className="hist-new" onClick={() => void newSession()}>
        + 新会话
      </button>
      <input className="hist-search" placeholder="搜索会话" value={kw} onChange={(e) => setKw(e.target.value)} />
      <div className="hist-list">
        {groups.length === 0 && <div className="hist-empty">暂无会话</div>}
        {groups.map((g) => (
          <div className={`hist-group ${closedGroups.has(g.name) ? 'collapsed' : ''}`} key={g.name}>
            <div className="hist-group-head" onClick={() => toggleGroup(g.name)}>
              <span className="g-dot" />
              <span className="g-name">{g.name}</span>
              <span className="g-count">{g.items.length}</span>
              <span className="g-toggle">▼</span>
            </div>
            <div className="hist-group-body">
              {g.items.map((s) => (
                <div
                  className={`hist-item ${current?.session_id === s.session_id ? 'active' : ''}`}
                  key={s.session_id}
                  onClick={() => void openSession(s.session_id)}
                >
                  <span className={`i-dot ${s.pending_interaction ? 'pend' : ''}`} />
                  <span className="i-title">{s.title ?? s.last_message ?? '新会话'}</span>
                  <span className="i-time">{fmtTime(s.updated_at)}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </>
  )
}
