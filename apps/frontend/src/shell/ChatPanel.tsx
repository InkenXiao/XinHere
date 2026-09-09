// 对话面板（核心）：消息流/执行态双 pane 过渡 + composer + 快捷标签 + 知识库选择
import { useEffect, useRef } from 'react'
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'
import type { Node } from '@/registry/ConversationAssembler'
import { toolZh } from '@/utils'
import ChatComposer from './ChatComposer'
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

  const streamRef = useRef<HTMLDivElement>(null)

  // 自动滚底
  useEffect(() => {
    const el = streamRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [snap])

  const doSend = (msg: string) => {
    const t = msg.trim()
    if (!t || sending || !current) return
    void send(t)
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
                <div className="welcome-t">信在此 · 新在此</div>
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
