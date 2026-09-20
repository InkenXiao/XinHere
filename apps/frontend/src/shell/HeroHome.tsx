// Xin语默认 hero：居中大问数框（ChatComposer 提供 文件/知识库/联网/模型/录音/发送 全真实功能）
// 发送 → 进入会话视图；输入框下方为用户自定义卡片（链接 / 技能，存于数据库）
import { useEffect, useState } from 'react'
import { api } from '@/transport/api'
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'
import { useTaskRunStore } from '@/state/taskRunStore'
import type { HeroCardItem, SendOptions } from '@/types'
import ChatComposer from './ChatComposer'
import HeroCardModal from './HeroCardModal'

export default function HeroHome() {
  const sending = useSessionStore((s) => s.sending)
  const toast = useUiStore((s) => s.toast)
  const [busy, setBusy] = useState(false)
  const [heroCards, setHeroCards] = useState<HeroCardItem[]>([])
  const [cardModalOpen, setCardModalOpen] = useState(false)

  const loadCards = () => {
    api<{ items: HeroCardItem[] }>('GET', '/hero/cards')
      .then((r) => setHeroCards(r.items ?? []))
      .catch(() => {})
  }

  useEffect(() => {
    loadCards()
  }, [])

  const enterChat = async (question: string, opts?: SendOptions) => {
    if (busy || sending) return
    setBusy(true)
    try {
      await useSessionStore.getState().newSession()
      useUiStore.getState().setWorkView('chat')
      const q = question?.trim()
      if (q) void useSessionStore.getState().send(q, opts)
    } finally {
      setBusy(false)
    }
  }

  // 自定义卡片：链接直接打开；技能后台执行（运行状态见 toast 提示）
  const openCard = (c: HeroCardItem) => {
    if (c.kind === 'link' && c.link_url) {
      window.open(c.link_url, '_blank')
      return
    }
    void useTaskRunStore.getState().startRun(c)
  }

  const removeCard = async (c: HeroCardItem) => {
    if (!window.confirm(`移除卡片「${c.name}」？`)) return
    try {
      await api('DELETE', `/hero/cards/${c.id}`)
      loadCards()
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
  }

  return (
    <div className="tw-anchor">
      <div className="tw-backdrop" />
      <div className="tw-title">
        <h1>全场景 AI 工作台</h1>
        <p className="sub">信在此 · 新在此</p>
      </div>
      <div className="tw-chat">
        <ChatComposer variant="hero" onSend={(t, opts) => void enterChat(t, opts)} />
      </div>
      {heroCards.length > 0 && (
        <div className="hero-cards">
          {heroCards.map((c) => (
            <div className="hero-card" key={c.id} onClick={() => openCard(c)}>
              <span className="hc-kind">{c.kind === 'link' ? '❡' : '⌘'}</span>
              <span className="hc-name" title={c.name}>
                {c.name}
              </span>
              <span
                className="hc-del"
                title="移除"
                onClick={(e) => {
                  e.stopPropagation()
                  void removeCard(c)
                }}
              >
                ×
              </span>
            </div>
          ))}
        </div>
      )}
      {cardModalOpen && <HeroCardModal onClose={() => setCardModalOpen(false)} onSaved={loadCards} />}
    </div>
  )
}
