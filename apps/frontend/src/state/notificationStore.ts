// 消息中心：列表/未读数 + 已读维护（30s 轮询兜底；任务流转即时产生通知）
import { create } from 'zustand'
import { api } from '@/transport/api'

export interface NotificationItem {
  notification_id: string
  category: 'todo_remind' | 'todo_submitted' | 'todo_completed' | 'todo_returned'
  title: string
  body: string
  todo_id: string
  task_id: string
  actor_id: string
  actor_name: string
  is_read: boolean
  created_at: string
}

interface NotificationState {
  items: NotificationItem[]
  unreadCount: number
  loading: boolean
  load: () => Promise<void>
  markRead: (id: string) => Promise<void>
  markAllRead: () => Promise<void>
  receive: (item: NotificationItem) => void
}

let pollTimer: ReturnType<typeof setInterval> | null = null

export const useNotificationStore = create<NotificationState>((set, get) => ({
  items: [],
  unreadCount: 0,
  loading: false,

  async load() {
    set({ loading: true })
    try {
      const r = await api<{ items: NotificationItem[]; unread_count: number }>('GET', '/notifications?limit=50')
      set({ items: r.items, unreadCount: r.unread_count })
    } catch {
      /* 静默：轮询兜底 */
    } finally {
      set({ loading: false })
    }
  },

  async markRead(id) {
    await api('POST', '/notifications/read', { notification_id: id })
    const items = get().items.map((n) =>
      n.notification_id === id ? { ...n, is_read: true } : n,
    )
    set({ items, unreadCount: items.filter((n) => !n.is_read).length })
  },

  async markAllRead() {
    await api('POST', '/notifications/read', {})
    const items = get().items.map((n) => ({ ...n, is_read: true }))
    set({ items, unreadCount: 0 })
  },

  receive(item) {
    // todo_bus SSE 推送（/todos/stream）到达时即时入列（去重）
    if (get().items.some((n) => n.notification_id === item.notification_id)) return
    set({ items: [item, ...get().items].slice(0, 100), unreadCount: get().unreadCount + (item.is_read ? 0 : 1) })
  },
}))

/** 登录后调用：启动轮询 */
export function startNotificationPolling() {
  if (pollTimer) return
  useNotificationStore.getState().load()
  pollTimer = setInterval(() => useNotificationStore.getState().load(), 30_000)
}

export function stopNotificationPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}
