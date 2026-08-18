// 历史对话栏：搜索过滤 + 按任务类型分组（默认折叠，平滑展开动画）+ pending 红点
import { useMemo, useState } from 'react'
import { useSessionStore } from '@/state/sessionStore'
import type { SessionListItem } from '@/types'
import { fmtTime } from '@/utils'

// 任务类型 → 分组展示元数据（key 对齐后端 skills.session_task_type 输出）
const TASK_TYPE_META: Record<string, { label: string; ico: string }> = {
  post_report: { label: '投后管理', ico: '📄' },
  fin_risk_report: { label: '财务风险报告', ico: '📊' },
  info_fill: { label: '信息填报', ico: '📝' },
  kpi_fill: { label: '经营者考核', ico: '🎯' },
  ms_feedback: { label: '里程碑反馈', ico: '🚩' },
  lamp_adjust: { label: '亮灯调整', ico: '💡' },
  task_stats: { label: '任务执行统计', ico: '📈' },
  generic_dispatch: { label: '通用派发', ico: '📮' },
  chat: { label: '普通对话', ico: '💬' },
}
// 分组排序：核心 3 技能优先，其余按使用频次语义排后，普通对话兜底
const GROUP_ORDER = [
  'info_fill', 'post_report', 'fin_risk_report',
  'kpi_fill', 'ms_feedback', 'lamp_adjust', 'task_stats', 'generic_dispatch', 'chat',
]

function typeOf(s: SessionListItem): string {
  const t = s.task_type ?? 'chat'
  return TASK_TYPE_META[t] ? t : 'chat'
}

export default function HistoryRail({ onCollapse }: { onCollapse?: () => void }) {
  const sessions = useSessionStore((s) => s.sessions)
  const current = useSessionStore((s) => s.current)
  const openSession = useSessionStore((s) => s.openSession)
  const newSession = useSessionStore((s) => s.newSession)
  const [kw, setKw] = useState('')
  // 默认全部折叠：openGroups 记录已展开的分组
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set())

  const groups = useMemo(() => {
    const k = kw.trim()
    const filtered = k
      ? sessions.filter((s) => (s.title ?? '').includes(k) || (s.last_message ?? '').includes(k))
      : sessions
    const map = new Map<string, SessionListItem[]>()
    for (const s of filtered) {
      const t = typeOf(s)
      if (!map.has(t)) map.set(t, [])
      map.get(t)!.push(s)
    }
    return GROUP_ORDER.filter((t) => (map.get(t) ?? []).length > 0).map((t) => ({
      key: t,
      label: TASK_TYPE_META[t].label,
      ico: TASK_TYPE_META[t].ico,
      items: map.get(t) ?? [],
    }))
  }, [sessions, kw])

  const toggleGroup = (g: string) => {
    const n = new Set(openGroups)
    if (n.has(g)) n.delete(g)
    else n.add(g)
    setOpenGroups(n)
  }

  const searching = kw.trim().length > 0

  return (
    <>
      <div className="col-head">
        <div className="t">
          <span className="h-ico">🕘</span>历史对话
        </div>
        <button className="icon-btn" onClick={onCollapse} title="收起">
          ‹
        </button>
      </div>
      <button className="hist-new" onClick={() => void newSession()}>
        + 新对话
      </button>
      <input className="hist-search" placeholder="搜索会话" value={kw} onChange={(e) => setKw(e.target.value)} />
      <div className="hist-list">
        {groups.length === 0 && <div className="hist-empty">暂无会话</div>}
        {groups.map((g) => {
          // 搜索时强制展开以展示匹配项；否则默认折叠
          const open = searching || openGroups.has(g.key)
          return (
            <div className={`hist-group ${open ? '' : 'collapsed'}`} key={g.key}>
              <div className="hist-group-head" onClick={() => toggleGroup(g.key)}>
                <span className="g-ico">{g.ico}</span>
                <span className="g-name">{g.label}</span>
                <span className="g-count">{g.items.length}</span>
                <span className="g-toggle">▼</span>
              </div>
              <div className="hist-group-body">
                <div className="hist-group-inner">
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
            </div>
          )
        })}
      </div>
    </>
  )
}
