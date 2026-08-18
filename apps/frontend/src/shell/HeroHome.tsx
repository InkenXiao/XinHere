// 屏1 默认 hero：标题「深耕问题」+ slogan + 大 AI 问数输入框
// + 3 核心技能卡（点击弹模版选择）+ 知识库/模型下拉（值对：label 展示 / value 传参）
// 发送/点「新对话」→ 进入三栏工作台视图
import { useEffect, useRef, useState } from 'react'
import { runtimeEnv } from '@/config'
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'

// 3 核心技能卡（点击 → 模版选择 Modal；skill_key 对齐后端 skills 目录）
const SKILL_CARDS = [
  { key: 'post_report', name: '投后管理报告', desc: '生成 Word 报告', icon: '📄' },
  { key: 'fin_risk_report', name: '财务风险报告', desc: '生成 PPT 演示', icon: '📊' },
  { key: 'info_fill', name: '信息填报', desc: '调查 / 填报 / 试算', icon: '📝' },
] as const

// 模型选项（值对关系：label 前端展示名，value 传后端/网关的模型参数）
const MODEL_OPTIONS = [{ label: runtimeEnv.MODEL_NAME || 'DeepSeek-V4-Flash', value: 'LLM' }]

// 知识库选项（先支持投后管理系统；label 展示，value 随 kb_ids 传后端）
const KB_OPTIONS = [{ label: '投后管理系统', value: 'post_investment' }]

export default function HeroHome() {
  const sending = useSessionStore((s) => s.sending)
  const modelValue = useUiStore((s) => s.modelValue)
  const heroKb = useUiStore((s) => s.heroKb)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [openDrop, setOpenDrop] = useState<'kb' | 'model' | null>(null)
  const toolsRef = useRef<HTMLDivElement>(null)

  // 点击 hero-tools 区域外收起下拉
  useEffect(() => {
    if (!openDrop) return
    const onDown = (e: MouseEvent) => {
      if (!toolsRef.current?.contains(e.target as globalThis.Node)) setOpenDrop(null)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [openDrop])

  const enterChat = async (question?: string) => {
    if (busy || sending) return
    setBusy(true)
    try {
      await useSessionStore.getState().newSession()
      useUiStore.getState().setWorkView('chat')
      const q = question?.trim()
      if (q) void useSessionStore.getState().send(q, heroKb.length > 0 ? heroKb : undefined)
    } finally {
      setBusy(false)
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
        <div className="hero-tools" ref={toolsRef}>
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
      <div
        className="hero-scroll-hint"
        onClick={() => document.getElementById('screen-dash')?.scrollIntoView({ behavior: 'smooth' })}
      >
        ↓ 下滑查看 · 下发的任务
      </div>
    </div>
  )
}
