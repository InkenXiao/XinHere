// XuanPu 消息中心：数字分身/AI 任务通知（经 XuanPu 平台按水位增量拉取）
// 轮询 30s + 页面可见时即时刷新；水位与最近消息持久化 localStorage（刷新不丢历史）
import { create } from 'zustand'
import { api } from '@/transport/api'

export interface XuanpuMsg {
  id: number
  source: string
  title: string
  body: string
  url: string
  ts: number
}

const LS_LAST_ID = 'xinhere.xuanpuMsg.lastId'
const LS_ITEMS = 'xinhere.xuanpuMsg.items'
const MAX_KEEP = 50
const POLL_MS = 30_000

function loadItems(): XuanpuMsg[] {
  try {
    const raw = localStorage.getItem(LS_ITEMS)
    return raw ? (JSON.parse(raw) as XuanpuMsg[]) : []
  } catch {
    return []
  }
}

interface XuanpuMsgState {
  items: XuanpuMsg[]
  lastId: number
  unread: number
  polling: boolean
  poll: () => Promise<void>
  startPolling: () => void
  stopPolling: () => void
  markRead: () => void
  openMsg: (msg: XuanpuMsg) => Promise<void>
}

let timer: ReturnType<typeof setInterval> | null = null

export const useXuanpuMsgStore = create<XuanpuMsgState>((set, get) => ({
  items: loadItems(),
  lastId: Number(localStorage.getItem(LS_LAST_ID) || 0),
  unread: 0,
  polling: false,

  poll: async () => {
    try {
      const r = await api<{ items?: XuanpuMsg[]; last_id?: number }>(
        'GET',
        `/xuanpu/messages?after_id=${get().lastId}`,
      )
      const fresh = (r.items ?? []).filter((m) => m && typeof m.id === 'number')
      if (fresh.length === 0 && typeof r.last_id !== 'number') return
      set((s) => {
        // 首次拉取（lastId=0）：只入列不提醒，避免历史消息刷未读
        const first = s.lastId === 0
        const items = [...fresh.slice().reverse(), ...s.items].slice(0, MAX_KEEP)
        const lastId = Math.max(s.lastId, r.last_id ?? 0, ...fresh.map((m) => m.id))
        localStorage.setItem(LS_LAST_ID, String(lastId))
        localStorage.setItem(LS_ITEMS, JSON.stringify(items))
        return { items, lastId, unread: first ? s.unread : s.unread + fresh.length }
      })
    } catch {
      // 平台不可达静默跳过，下轮重试
    }
  },

  startPolling: () => {
    if (timer !== null) return
    set({ polling: true })
    void get().poll()
    timer = setInterval(() => {
      if (document.visibilityState === 'visible') void get().poll()
    }, POLL_MS)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') void get().poll()
    })
  },

  stopPolling: () => {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
    set({ polling: false })
  },

  markRead: () => set({ unread: 0 }),

  openMsg: async (msg) => {
    if (!msg.url) return
    try {
      const r = await api<{ url: string; first_login: boolean }>('POST', '/xuanpu/open', {
        url: msg.url,
      })
      if (r.first_login) window.alert('首次访问需在 XuanPu 登录一次')
      window.open(r.url, '_blank')
    } catch (e: unknown) {
      window.alert(e instanceof Error ? e.message : String(e))
    }
  },
}))
