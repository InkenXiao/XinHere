// 对话面板（核心）：消息流/执行态双 pane 过渡 + composer + 快捷标签 + 知识库选择
import { useEffect, useMemo, useRef, useState } from 'react'
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'
import { api } from '@/transport/api'
import type { KbSource } from '@/types'
import type { Node } from '@/registry/ConversationAssembler'
import { toolZh } from '@/utils'
import ExecutionView from './ExecutionView'

const QUICK_TAGS = ['发起风险填报', '现金保障试算', '任务执行统计', '生成投后报告']
const EXAMPLES = ['帮我发起 8 月风险填报', '查一下本周任务完成率', '生成 7 月投后报告']

export default function ChatPanel() {
  const current = useSessionStore((s) => s.current)
  const snap = useSessionStore((s) => s.snap)
  const sending = useSessionStore((s) => s.sending)
  const send = useSessionStore((s) => s.send)
  const cancel = useSessionStore((s) => s.cancel)
  const componentEmit = useSessionStore((s) => s.componentEmit)
  const executing = useUiStore((s) => s.executing)

  const [text, setText] = useState('')
  const [kbOpen, setKbOpen] = useState(false)
  const [kbSources, setKbSources] = useState<KbSource[] | null>(null)
  const [kbSel, setKbSel] = useState<Set<string>>(new Set())
  const streamRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)

  // 自动滚底
  useEffect(() => {
    const el = streamRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [snap])

  // 知识库：首次展开时加载
  useEffect(() => {
    if (kbOpen && kbSources === null) {
      api<{ items: KbSource[] }>('GET', '/kb/sources')
        .then((r) => setKbSources(r.items ?? []))
        .catch(() => setKbSources([]))
    }
  }, [kbOpen, kbSources])

  const kbGroups = useMemo(() => {
    const list = kbSources ?? []
    const roots = list.filter((x) => x.parent_id === null)
    return roots.map((r) => ({ root: r, leaves: list.filter((x) => x.parent_id === r.kb_id) }))
  }, [kbSources])

  const doSend = (msg: string) => {
    const t = msg.trim()
    if (!t || sending || !current) return
    setText('')
    if (taRef.current) taRef.current.style.height = ''
    void send(t, kbSel.size > 0 ? [...kbSel] : undefined)
  }

  const toggleKb = (id: string) => {
    const n = new Set(kbSel)
    if (n.has(id)) n.delete(id)
    else n.add(id)
    setKbSel(n)
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
            {node.content}
            {node.usage && (
              <div style={{ marginTop: 6, fontSize: 11, color: 'var(--ink-30)' }}>
                tokens: {node.usage.prompt}+{node.usage.completion}
              </div>
            )}
          </div>
        )
      case 'steps':
        return (
          <div className="steps" key={node.key}>
            {node.items.map((s) => (
              <span className={`step ${s.status}`} key={s.step}>
                <span className="dot" />
                步骤 {s.step}
              </span>
            ))}
          </div>
        )
      case 'tool': {
        const args = JSON.stringify(node.args)
        return (
          <div className="tool-card" key={node.key}>
            <div className="tc-hd">
              <span className="tc-name">🔧 {toolZh(node.name)}</span>
              <span className={`tc-status ${node.status}`}>
                {node.status === 'running' ? '执行中' : node.status === 'done' ? '完成' : '失败'}
              </span>
            </div>
            {args !== '{}' && <div className="tc-args">{args.length > 120 ? `${args.slice(0, 120)}…` : args}</div>}
            {node.result && <div className="tc-result">{node.result}</div>}
          </div>
        )
      }
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
        <button className="chat-new" onClick={() => void useSessionStore.getState().newSession()}>
          + 新会话
        </button>
      </div>
      <div className="chat-viewport">
        <div className={`chat-pane ${executing ? 'hidden-pane' : ''}`}>
          <div className="msg-stream" ref={streamRef}>
            {empty && (
              <div className="welcome">
                <div className="welcome-ico">✦</div>
                <div className="welcome-t">新在这里，心在这里</div>
                <div className="welcome-h" style={{ marginBottom: 14 }}>
                  用自然语言发起任务、追踪进度，组件在对话中直接操作
                </div>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
                  {EXAMPLES.map((q) => (
                    <button className="tag-chip" key={q} onClick={() => doSend(q)}>
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {snap.nodes.map(renderNode)}
            {snap.streaming && (
              <div className="msg-ai">
                {snap.streaming.text}
                <span className="cursor-blink" />
              </div>
            )}
          </div>
          <div className="tags-row">
            {QUICK_TAGS.map((t) => (
              <button className="tag-chip" key={t} onClick={() => doSend(t)}>
                {t}
              </button>
            ))}
          </div>
          <div className="composer">
            <div className="input-pill">
              <textarea
                ref={taRef}
                rows={1}
                placeholder={current ? '输入消息，Enter 发送 / Shift+Enter 换行' : '请先选择或新建会话'}
                value={text}
                disabled={!current}
                onChange={(e) => setText(e.target.value)}
                onInput={(e) => {
                  const el = e.currentTarget
                  el.style.height = 'auto'
                  el.style.height = `${Math.min(el.scrollHeight, 140)}px`
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    doSend(text)
                  }
                }}
              />
              <button
                className="send-pill"
                disabled={!current || (!sending && !text.trim())}
                onClick={() => (sending ? void cancel() : doSend(text))}
              >
                {sending ? '停止' : '发送'}
              </button>
            </div>
          </div>
          <div className="bottom-bar">
            <span className={`bottom-item ${kbOpen ? 'on' : ''}`} onClick={() => setKbOpen((v) => !v)}>
              📚 知识库
              {kbSel.size > 0 && <span className="kb-sel-count">{kbSel.size}</span>}
            </span>
            <div className={`kb-popover ${kbOpen ? 'show' : ''}`}>
              <div className="kb-pop-head">
                <h4>选择知识库</h4>
                <button className="icon-btn" onClick={() => setKbOpen(false)}>
                  ×
                </button>
              </div>
              <div className="kb-tree">
                {kbSources === null && <div className="kb-group-t">加载中…</div>}
                {kbGroups.map(({ root, leaves }) =>
                  leaves.length === 0 ? (
                    <KbLeaf key={root.kb_id} src={root} sel={kbSel.has(root.kb_id)} onToggle={toggleKb} />
                  ) : (
                    <div key={root.kb_id}>
                      <div className="kb-group-t">{root.name}</div>
                      {leaves.map((l) => (
                        <KbLeaf key={l.kb_id} src={l} sel={kbSel.has(l.kb_id)} onToggle={toggleKb} />
                      ))}
                    </div>
                  ),
                )}
              </div>
            </div>
          </div>
        </div>
        <ExecutionView />
      </div>
    </div>
  )
}

function KbLeaf({ src, sel, onToggle }: { src: KbSource; sel: boolean; onToggle: (id: string) => void }) {
  return (
    <div className={`kb-leaf ${sel ? 'sel' : ''}`} onClick={() => onToggle(src.kb_id)}>
      <span className="cbx">{sel ? '✓' : ''}</span>
      {src.name}
      <span className={`kb-badge ${src.kb_type === 'internal' ? 'h' : 'n'}`}>
        {src.kb_type === 'internal' ? '内部' : '外部'}
      </span>
    </div>
  )
}
