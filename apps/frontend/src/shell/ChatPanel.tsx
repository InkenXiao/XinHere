// 对话面板（核心）：消息流/执行态双 pane 过渡 + composer + 知识库选择
// 输出效果参照 XuanPu：执行过程折叠组（模型调用+工具轨迹）、技能卡、结论块、呼吸状态行
import { useEffect, useMemo, useRef } from 'react'
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'
import type { Node } from '@/registry/ConversationAssembler'
import { toolZh } from '@/utils'
import ChatComposer from './ChatComposer'
import ExecutionView from './ExecutionView'
import Markdown from './Markdown'
import TraceGroup, { type TraceState } from './TraceGroup'

/** 渲染项：普通节点 或 按 turn 聚合的执行过程组 */
type RenderItem =
  | { kind: 'node'; key: string; node: Node }
  | { kind: 'trace'; key: string; turn: number; items: Node[]; state: TraceState }

const OUTCOME_META: Record<string, { cls: string; title: string; icon: string }> = {
  completed: { cls: 'ok', title: '任务已完成', icon: '✓' },
  blocked: { cls: 'warn', title: '任务已部分完成', icon: '!' },
  max_tokens: { cls: 'warn', title: '任务已部分完成', icon: '!' },
  aborted: { cls: 'warn', title: '任务已中断', icon: '⏸' },
  error: { cls: 'err', title: '任务已中断', icon: '✕' },
  crashed: { cls: 'err', title: '任务已中断', icon: '✕' },
}

function fmtElapsed(ms?: number): string {
  if (!ms || ms <= 0) return ''
  const sec = Math.max(1, Math.round(ms / 1000))
  return sec >= 60 ? `${Math.floor(sec / 60)} 分 ${sec % 60} 秒` : `${sec} 秒`
}

function fmtTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

/** 把事件节点装配为渲染序列：model/tool 节点按 turn 收进执行过程组（组在该 turn 首个轨迹项位置） */
function toRenderItems(nodes: Node[], running: boolean, curTurn: number): RenderItem[] {
  const outcomeByTurn = new Map<number, string>()
  for (const n of nodes) if (n.type === 'outcome') outcomeByTurn.set(n.turn, n.reason)
  const traced = new Set<number>()
  const activeTurns = new Set<number>() // 有过程活动（模型多轮/工具/技能）的 turn，结论块仅对这些轮次收尾
  const out: RenderItem[] = []
  for (const n of nodes) {
    if (n.type === 'model' || n.type === 'tool') {
      if (traced.has(n.turn)) continue
      traced.add(n.turn)
      activeTurns.add(n.turn)
      const items = nodes.filter((m) => (m.type === 'model' || m.type === 'tool') && m.turn === n.turn)
      let state: TraceState
      if (running && curTurn === n.turn) state = 'running'
      else {
        const reason = outcomeByTurn.get(n.turn)
        state = reason === 'aborted' ? 'interrupted' : reason === 'error' || reason === 'crashed' ? 'error' : 'done'
      }
      out.push({ kind: 'trace', key: `trace:${n.turn}`, turn: n.turn, items, state })
      continue
    }
    if (n.type === 'steps') continue // 步骤进度已由执行过程组的模型调用轨迹承载
    if (n.type === 'skill') activeTurns.add(n.turn)
    if (n.type === 'outcome' && !activeTurns.has(n.turn)) continue // 纯问答轮次不渲染结论块
    out.push({ kind: 'node', key: n.key, node: n })
  }
  return out
}

export default function ChatPanel() {
  const current = useSessionStore((s) => s.current)
  const snap = useSessionStore((s) => s.snap)
  const sending = useSessionStore((s) => s.sending)
  const send = useSessionStore((s) => s.send)
  const cancel = useSessionStore((s) => s.cancel)
  const componentEmit = useSessionStore((s) => s.componentEmit)
  const executing = useUiStore((s) => s.executing)

  const streamRef = useRef<HTMLDivElement>(null)

  // 自动滚底
  useEffect(() => {
    const el = streamRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [snap])

  const renderItems = useMemo(
    () => toRenderItems(snap.nodes, snap.running, snap.turn),
    [snap.nodes, snap.running, snap.turn],
  )

  // 呼吸状态行文案：工具执行中 > 流式生成 > 思考中（回合结束整行隐藏）
  const runningToolName = (() => {
    for (let i = snap.nodes.length - 1; i >= 0; i--) {
      const n = snap.nodes[i]
      if (n.type === 'tool' && n.status === 'running') return n.name
    }
    return ''
  })()
  const breathText = runningToolName
    ? `正在调用「${toolZh(runningToolName)}」…`
    : snap.streaming
      ? '正在生成回复…'
      : '正在思考…'

  const renderSkillCard = (node: Extract<Node, { type: 'skill' }>) => (
    <div className="chat-trace chat-skill open" key={node.key}>
      <div className="chat-trace__head">
        <span className="chat-trace__ico">⚙</span>
        <span className="chat-trace__name">技能执行</span>
        <span className="chat-trace__badge">{node.source === 'local' ? '本地技能' : '平台技能'}</span>
        {node.auto && <span className="chat-trace__badge chat-trace__badge--auto">自动识别</span>}
        <span className="chat-trace__chev" />
      </div>
      <div className="chat-trace__body">
        <div className="tc-plan__objective">
          已加载技能「{node.name}」，将按技能约定逐步执行
        </div>
      </div>
    </div>
  )

  const renderOutcome = (node: Extract<Node, { type: 'outcome' }>) => {
    const meta = OUTCOME_META[node.reason] ?? OUTCOME_META.completed
    const bits: string[] = []
    if (node.rounds > 0) bits.push(`共执行 ${node.rounds} 轮`)
    const elapsed = fmtElapsed(node.elapsedMs)
    if (elapsed) bits.push(`用时 ${elapsed}`)
    if (node.tokens) bits.push(`${fmtTokens(node.tokens)} Tokens`)
    return (
      <div className={`chat-outcome chat-outcome--${meta.cls}`} key={node.key}>
        <span className="chat-outcome__icon">{meta.icon}</span>
        <div className="chat-outcome__txt">
          <b>{meta.title}</b>
          {bits.length > 0 && <span>{bits.join(' · ')}</span>}
        </div>
      </div>
    )
  }

  const renderNode = (node: Node) => {
    switch (node.type) {
      case 'user':
        return (
          <div className="msg-user" key={node.key}>
            {node.content}
          </div>
        )
      case 'inject':
        return (
          <div className="msg-inject" key={node.key}>
            系统注入 · {node.content}
          </div>
        )
      case 'assistant':
        return (
          <div className="msg-ai" key={node.key}>
            <Markdown text={node.content} />
            {node.usage && (
              <div style={{ marginTop: 6, fontSize: 11, color: 'var(--ink-30)' }}>
                tokens: {node.usage.prompt}+{node.usage.completion}
              </div>
            )}
          </div>
        )
      case 'skill':
        return renderSkillCard(node)
      case 'outcome':
        return renderOutcome(node)
      case 'component': {
        const C = node.def.component
        return (
          <C
            key={node.key}
            base={node.base}
            state={node.state}
            emit={componentEmit(node.base.component_id)}
            disabled={node.base.status !== 'open'}
          />
        )
      }
      case 'error':
        return (
          <div className="err-card" key={node.key}>
            <b>{node.code}</b> {node.message}
          </div>
        )
      default:
        return null
    }
  }

  const empty = snap.nodes.length === 0 && !snap.streaming

  return (
    <div className="dialog-card">
      <div className="chat-head">
        <div className="chat-title">
          {current?.title ?? '新对话'}
          {current?.title && <span className="chat-sub">{current.session_id}</span>}
        </div>
        <button className="chat-new" onClick={() => useUiStore.getState().setWorkView('hero')}>
          + 新会话
        </button>
      </div>
      <div className="chat-viewport">
        <div className={`chat-pane ${executing ? 'hidden-pane' : ''}`}>
          <div className="msg-stream" ref={streamRef}>
            {empty && (
              <div className="welcome">
                <div className="welcome-ico">✦</div>
                <div className="welcome-t">信在此 · 新在此</div>
                <div className="welcome-h">用自然语言发起任务、追踪进度，组件在对话中直接操作</div>
              </div>
            )}
            {renderItems.map((it) =>
              it.kind === 'trace' ? (
                <TraceGroup key={it.key} items={it.items} state={it.state} />
              ) : (
                renderNode(it.node)
              ),
            )}
            {snap.streaming && (
              <div className="msg-ai">
                <Markdown text={snap.streaming.text} />
                <span className="cursor-blink" />
              </div>
            )}
            {snap.running && (
              <div className="chat-breath">
                <i />
                {breathText}
              </div>
            )}
          </div>
          <div className="composer">
            <ChatComposer
              variant="chat"
              disabled={!current}
              sending={sending}
              onCancel={() => void cancel()}
              onSend={(t, opts) => void send(t, opts)}
            />
          </div>
        </div>
        <ExecutionView />
      </div>
    </div>
  )
}
