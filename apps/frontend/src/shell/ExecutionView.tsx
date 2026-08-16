// 执行态视图：与对话 pane 交叉过渡（executing 控制）；时间线 + 组件进度
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'
import type { Node } from '@/registry/ConversationAssembler'
import { toolZh } from '@/utils'

const KIND_ZH: Record<string, string> = {
  'risk-dispatch-confirm': '发起确认',
  'risk-fill-form': '风险填报',
  'cash-guarantee-form': '现金试算',
  'kpi-fill-form': '考核填报',
  'ms-feedback-form': '里程碑反馈',
  'lamp-adjust-panel': '亮灯调整',
  'pit-report-view': '投后报告',
  'kanban-card': '批次看板',
}

interface EtItem {
  key: string
  name: string
  sub: string
  status: 'active' | 'done'
}

function collect(nodes: Node[]): EtItem[] {
  const out: EtItem[] = []
  for (const n of nodes) {
    if (n.type === 'steps') {
      for (const s of n.items) {
        out.push({
          key: `${n.key}:${s.step}`,
          name: `步骤 ${s.step}`,
          sub: s.status === 'done' ? '完成' : '进行中',
          status: s.status === 'done' ? 'done' : 'active',
        })
      }
    } else if (n.type === 'tool') {
      const firstLine = (n.result ?? '').split('\n')[0]
      out.push({
        key: n.key,
        name: toolZh(n.name),
        sub: n.status === 'running' ? '执行中' : firstLine.length > 60 ? `${firstLine.slice(0, 60)}…` : firstLine || '完成',
        status: n.status === 'running' ? 'active' : 'done',
      })
    } else if (n.type === 'component') {
      out.push({
        key: n.key,
        name: KIND_ZH[n.base.kind] ?? n.base.kind,
        sub: n.base.status === 'open' ? '待提交' : n.base.status === 'submitted' ? '已提交' : '已取消',
        status: n.base.status === 'open' ? 'active' : 'done',
      })
    }
  }
  return out
}

export default function ExecutionView() {
  const executing = useUiStore((s) => s.executing)
  const execDone = useUiStore((s) => s.execDone)
  const setExecuting = useUiStore((s) => s.setExecuting)
  const snap = useSessionStore((s) => s.snap)
  const current = useSessionStore((s) => s.current)

  const items = collect(snap.nodes)
  const comps = snap.nodes.filter((n): n is Extract<Node, { type: 'component' }> => n.type === 'component')
  const compsDone = comps.filter((n) => n.base.status !== 'open').length
  const pct = comps.length > 0 ? Math.round((compsDone / comps.length) * 100) : 0

  return (
    <div className={`exec-view ${executing ? '' : 'hidden-pane'}`}>
      <div className={`exec-head ${execDone ? 'done' : ''}`}>
        <span className="eh-dot" />
        <span className="eh-title">{execDone ? '执行完成' : '任务执行中'}</span>
        <span className="eh-sub">{current?.title ?? ''}</span>
        <button className="exec-back" onClick={() => setExecuting(false)}>
          返回对话
        </button>
      </div>
      <div className="exec-body">
        <div className="exec-timeline">
          {items.length === 0 && <div className="et-sub">暂无执行记录</div>}
          {items.map((it) => (
            <div className={`et-item ${it.status}`} key={it.key}>
              <span className="et-dot" />
              <div className="et-main">
                <div className="et-name">{it.name}</div>
                <div className="et-sub">{it.sub}</div>
              </div>
            </div>
          ))}
        </div>
        {comps.length > 0 && (
          <div className="exec-mini">
            <div className="em-title">🧩 组件进度</div>
            <div className="exec-progress">
              <i style={{ width: `${pct}%` }} />
            </div>
            <div className="et-sub" style={{ marginTop: 8 }}>
              组件 {compsDone}/{comps.length} 已完成
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
