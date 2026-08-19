// 对话面板（核心）：消息流/执行态双 pane 过渡 + 技能快捷入口 + composer + 知识库/模型下拉（均与首页 hero 对齐设计稿）
import { useEffect, useRef, useState } from 'react'
import { useSessionStore } from '@/state/sessionStore'
import { KB_OPTIONS, MODEL_OPTIONS, SKILL_CARDS, SKILL_PROMPTS, useUiStore } from '@/state/uiStore'
import type { Node } from '@/registry/ConversationAssembler'
import { toolZh } from '@/utils'
import ExecutionView from './ExecutionView'

export default function ChatPanel() {
  const current = useSessionStore((s) => s.current)
  const snap = useSessionStore((s) => s.snap)
  const sending = useSessionStore((s) => s.sending)
  const send = useSessionStore((s) => s.send)
  const cancel = useSessionStore((s) => s.cancel)
  const componentEmit = useSessionStore((s) => s.componentEmit)
  const executing = useUiStore((s) => s.executing)
  const modelValue = useUiStore((s) => s.modelValue)
  const heroKb = useUiStore((s) => s.heroKb)

  const [text, setText] = useState('')
  const [openDrop, setOpenDrop] = useState<'kb' | 'model' | null>(null)
  const streamRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const toolsRef = useRef<HTMLDivElement>(null)

  // 自动滚底
  useEffect(() => {
    const el = streamRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [snap])

  // 点击底部工具区外收起下拉
  useEffect(() => {
    if (!openDrop) return
    const onDown = (e: MouseEvent) => {
      if (!toolsRef.current?.contains(e.target as globalThis.Node)) setOpenDrop(null)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [openDrop])

  const doSend = (msg: string) => {
    const t = msg.trim()
    if (!t || sending || !current) return
    setText('')
    if (taRef.current) taRef.current.style.height = ''
    void send(t, heroKb.length > 0 ? heroKb : undefined)
  }

  // 技能快捷入口（与首页 hero 技能卡同行为）：报告类 → AI 意图识别；信息填报 → 模版选择
  const onSkill = (key: string) => {
    const prompt = SKILL_PROMPTS[key]
    if (!prompt) {
      useUiStore.getState().openTemplateModal(key)
      return
    }
    if (current) doSend(prompt)
    else {
      void useSessionStore.getState().newSession().then(() => doSend(prompt))
    }
  }

  const toggleKb = (value: string) => {
    const next = heroKb.includes(value) ? heroKb.filter((v) => v !== value) : [...heroKb, value]
    useUiStore.getState().setHeroKb(next)
  }

  const modelLabel = MODEL_OPTIONS.find((m) => m.value === modelValue)?.label ?? MODEL_OPTIONS[0].label
  const kbLabel =
    heroKb.length === 0
      ? '选择知识库'
      : KB_OPTIONS.filter((k) => heroKb.includes(k.value))
          .map((k) => k.label)
          .join('、') || '选择知识库'

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
          <span className="chat-area-tag">✦ AI 问数区域</span>
          <span className="chat-sub">你与门户助手的所有对话都在这里进行，输入或选择任务即可开始处理。</span>
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
                <div className="welcome-h">
                  用自然语言发起任务、追踪进度，组件在对话中直接操作
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
            {SKILL_CARDS.map((c) => (
              <button className="skill-pill" key={c.key} onClick={() => onSkill(c.key)}>
                <span className="s-ico">{c.icon}</span>
                {c.name}
              </button>
            ))}
          </div>
          <div className="composer">
            <div className="input-pill">
              <textarea
                ref={taRef}
                rows={1}
                placeholder={current ? '问问门户：本季度投资收益如何？帮我生成一份投后报告。' : '请先选择或新建会话'}
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
                {sending ? '停止' : 'AI 问数'}
              </button>
            </div>
          </div>
          <div className="bottom-bar" ref={toolsRef}>
            <span className="hero-select">
              <button
                className={`hero-tool-item ${heroKb.length > 0 ? 'on' : ''}`}
                onClick={() => setOpenDrop((v) => (v === 'kb' ? null : 'kb'))}
                title="选择知识库"
              >
                <svg className="t-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" width="14" height="14">
                  <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 19.5A2.5 2.5 0 0 0 6.5 22H20V2H6.5A2.5 2.5 0 0 0 4 4.5v15z" />
                </svg>
                {kbLabel}
                <span className="caret">▾</span>
              </button>
              {openDrop === 'kb' && (
                <div className="hero-drop" role="listbox">
                  {KB_OPTIONS.map((k) => (
                    <div
                      className={`hero-drop-item ${heroKb.includes(k.value) ? 'sel' : ''}`}
                      key={k.value}
                      onClick={() => toggleKb(k.value)}
                    >
                      <span className="tick">{heroKb.includes(k.value) ? '✓' : ''}</span>
                      {k.label}
                    </div>
                  ))}
                </div>
              )}
            </span>
            <span className="divider" />
            <span className="hero-select">
              <button
                className="model-chip"
                onClick={() => setOpenDrop((v) => (v === 'model' ? null : 'model'))}
                title="当前模型"
              >
                <svg className="m-ico" viewBox="0 0 24 24" fill="currentColor" width="14" height="14">
                  <path d="M12 2l2.5 8.5L23 12l-8.5 1.5L12 22l-2.5-8.5L1 12l8.5-1.5z" />
                </svg>
                {modelLabel}
                <span className="caret">▾</span>
              </button>
              {openDrop === 'model' && (
                <div className="hero-drop" role="listbox">
                  {MODEL_OPTIONS.map((m) => (
                    <div
                      className={`hero-drop-item ${m.value === modelValue ? 'sel' : ''}`}
                      key={m.value}
                      onClick={() => {
                        useUiStore.getState().setModelValue(m.value)
                        setOpenDrop(null)
                      }}
                    >
                      <span className="tick">{m.value === modelValue ? '✓' : ''}</span>
                      {m.label}
                    </div>
                  ))}
                </div>
              )}
            </span>
          </div>
        </div>
        <ExecutionView />
      </div>
    </div>
  )
}
