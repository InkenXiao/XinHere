// 屏1 默认 hero（类 index1）：大标题 + slogan + 大 AI 问数输入框
// 发送/点「新对话」→ 进入三栏工作台视图
import { useState } from 'react'
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'

const HERO_CHIPS = ['发起风险填报', '现金保障试算', '生成投后报告', '任务执行统计']

export default function HeroHome() {
  const sending = useSessionStore((s) => s.sending)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  const enterChat = async (question?: string) => {
    if (busy || sending) return
    setBusy(true)
    try {
      await useSessionStore.getState().newSession()
      useUiStore.getState().setWorkView('chat')
      const q = question?.trim()
      if (q) void useSessionStore.getState().send(q)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="hero">
      <div className="hero-inner">
        <h1>全场景 AI 工作台</h1>
        <p className="hero-slogan">Fall in love with the problem, not the solution.</p>
        <div className="ask">
          <svg className="lead" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M12 2l2.5 8.5L23 12l-8.5 1.5L12 22l-2.5-8.5L1 12l8.5-1.5z" />
          </svg>
          <input
            value={text}
            placeholder="问问门户：本季度投资收益如何？帮我生成一份投后报告."
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void enterChat(text)
            }}
          />
          <button className="go" disabled={busy || sending} onClick={() => void enterChat(text)}>
            <span className="arr">→</span> AI 问答
          </button>
        </div>
        <div className="hero-actions">
          {HERO_CHIPS.map((c) => (
            <button className="chip" key={c} onClick={() => void enterChat(c)}>
              {c}
            </button>
          ))}
          <button className="chip chip-new" onClick={() => void enterChat()}>
            ＋ 新对话
          </button>
        </div>
      </div>
      <div
        className="hero-scroll-hint"
        onClick={() => document.getElementById('screen-dash')?.scrollIntoView({ behavior: 'smooth' })}
      >
        ↓ 下滑查看业务看板
      </div>
    </div>
  )
}
