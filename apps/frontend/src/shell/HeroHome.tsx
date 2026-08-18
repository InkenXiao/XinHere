// 屏1 默认 hero：标题「深耕问题」+ slogan + 大 AI 问数输入框
// + 3 核心技能卡（点击弹模版选择）+ 知识库/模型 chip
// 发送/点「新对话」→ 进入三栏工作台视图
import { useState } from 'react'
import { runtimeEnv } from '@/config'
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'

// 3 核心技能卡（点击 → 模版选择 Modal；skill_key 对齐后端 skills 目录）
const SKILL_CARDS = [
  { key: 'post_report', name: '投后管理报告', desc: '生成 Word 报告', icon: '📄' },
  { key: 'fin_risk_report', name: '财务风险报告', desc: '生成 PPT 演示', icon: '📊' },
  { key: 'info_fill', name: '信息填报', desc: '调查 / 填报 / 试算', icon: '📝' },
] as const

export default function HeroHome() {
  const sending = useSessionStore((s) => s.sending)
  const toast = useUiStore((s) => s.toast)
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
        <h1>深耕问题</h1>
        <p className="hero-slogan">“Fall in love with the problem, not the solution.”</p>
        <div className="ask">
          <svg className="lead" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M12 2l2.5 8.5L23 12l-8.5 1.5L12 22l-2.5-8.5L1 12l8.5-1.5z" />
          </svg>
          <input
            value={text}
            placeholder="问问门户：本季度投资收益如何？帮我生成一份投后报告。"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void enterChat(text)
            }}
          />
          <button className="go" disabled={busy || sending} onClick={() => void enterChat(text)}>
            <span className="arr">→</span> AI 问数
          </button>
        </div>
        <div className="hero-skills">
          {SKILL_CARDS.map((c) => (
            <button
              className="skill-pill"
              key={c.key}
              onClick={() => useUiStore.getState().openTemplateModal(c.key)}
            >
              <span className="s-ico">{c.icon}</span>
              {c.name}
              <span className="s-type">{c.desc}</span>
            </button>
          ))}
        </div>
        <div className="hero-tools">
          <button
            className="hero-tool-item"
            onClick={() => toast('知识库选择开发中，敬请期待')}
            title="选择知识库"
          >
            <svg className="t-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" width="14" height="14">
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 19.5A2.5 2.5 0 0 0 6.5 22H20V2H6.5A2.5 2.5 0 0 0 4 4.5v15z" />
            </svg>
            选择知识库
            <span className="caret">▾</span>
          </button>
          <span className="divider" />
          <span className="model-chip" title="当前模型">
            <svg className="m-ico" viewBox="0 0 24 24" fill="currentColor" width="14" height="14">
              <path d="M12 2l2.5 8.5L23 12l-8.5 1.5L12 22l-2.5-8.5L1 12l8.5-1.5z" />
            </svg>
            {runtimeEnv.MODEL_NAME || 'DeepSeek-V4-Flash'}
            <span className="caret">▾</span>
          </span>
        </div>
      </div>
      <div
        className="hero-scroll-hint"
        onClick={() => document.getElementById('screen-dash')?.scrollIntoView({ behavior: 'smooth' })}
      >
        ↓ 下滑查看 · 下发的任务
      </div>
    </div>
  )
}
