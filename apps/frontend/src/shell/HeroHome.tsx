// 瞭望塔默认 hero：居中大问数框（attach 徽标为视觉态，真实知识库选择在会话内）
// 发送/点快捷 chip → 进入会话视图
import { useState } from 'react'
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'

const HERO_CHIPS = ['发起风险填报', '现金保障试算', '生成投后报告', '任务执行统计']

export default function HeroHome() {
  const sending = useSessionStore((s) => s.sending)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [attachOpen, setAttachOpen] = useState(false)
  const [attachMode, setAttachMode] = useState<'kb' | 'skill' | null>(null)

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

  const pickAttach = (m: 'kb' | 'skill') => {
    setAttachMode((cur) => (cur === m ? null : m))
    setAttachOpen(false)
  }

  return (
    <div className="tw-anchor">
      <div className="tw-backdrop" />
      <div className="tw-title">
        <h1>全场景 AI 工作台</h1>
        <p className="sub">新在这里 · 心在这里</p>
      </div>
      <div className="tw-chat">
        <div className="inp">
          <button
            className={`attach ${attachMode ? 'active' : ''}`}
            title="调用知识库 / 使用 Skill"
            onClick={() => setAttachOpen((v) => !v)}
          >
            +
          </button>
          <span className={`badge ${attachMode ? 'show' : ''}`}>
            <span>{attachMode === 'skill' ? '行研 Skill' : '知识库'}</span>
            <span className="x" title="移除" onClick={() => setAttachMode(null)}>
              ×
            </span>
          </span>
          <input
            type="text"
            value={text}
            placeholder="向 XinHere 提问…"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void enterChat(text)
            }}
          />
          <button className="send" disabled={busy || sending} onClick={() => void enterChat(text)}>
            发送
          </button>
        </div>
      </div>
      <div className={`tw-attach-pop ${attachOpen ? 'open' : ''}`}>
        <div className={`opt ${attachMode === 'kb' ? 'active' : ''}`} onClick={() => pickAttach('kb')}>
          <span className="ic">❡</span>调用知识库
        </div>
        <div className={`opt ${attachMode === 'skill' ? 'active' : ''}`} onClick={() => pickAttach('skill')}>
          <span className="ic">⌘</span>使用行研报告 Skill
        </div>
      </div>
      <div className="tw-chips">
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
  )
}
