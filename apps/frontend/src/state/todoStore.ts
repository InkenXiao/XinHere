// 待办：列表/忽略集合(sessionStorage)/todo-changed 触发刷新 + 30s 轮询兜底
import { create } from 'zustand'
import { api } from '@/transport/api'
import type { TodoItem, XuanPuFillAssignment, XuanPuTodos } from '@/types'

const IGNORE_KEY = 'xinhere.todo.ignored'

function loadIgnored(): Set<string> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(IGNORE_KEY) || '[]') as string[])
  } catch {
    return new Set()
  }
}

function saveIgnored(s: Set<string>) {
  try {
    sessionStorage.setItem(IGNORE_KEY, JSON.stringify([...s]))
  } catch {
    /* ignore */
  }
}

interface TodoState {
  items: TodoItem[]
  xpFills: XuanPuFillAssignment[]
  ignored: Set<string>
  loading: boolean
  load: () => Promise<void>
  ignore: (todoId: string) => void
  feedback: (todoId: string, text: string) => Promise<void>
  na: (todoId: string, reason: string) => Promise<void>
  naConfirm: (todoId: string) => Promise<void>
  naReject: (todoId: string, comment?: string) => Promise<void>
  complete: (todoId: string) => Promise<void>
}

let pollTimer: ReturnType<typeof setInterval> | null = null

export const useTodoStore = create<TodoState>((set, get) => ({
  items: [],
  xpFills: [],
  ignored: loadIgnored(),
  loading: false,

  async load() {
    set({ loading: true })
    try {
      // 仅拉取需要我办理的待办
      const r = await api<{ items: TodoItem[] }>('GET', '/todos?box=assignee')
      set({ items: r.items })
    } finally {
      set({ loading: false })
    }
    // XuanPu 填报待办一并拉取（平台不可达时静默，不影响本地待办）
    api<XuanPuTodos>('GET', '/xuanpu/todos?owner=')
      .then((r) => set({ xpFills: r.raw ? [] : r.fill_assignments ?? [] }))
      .catch(() => {})
  },

  ignore(todoId) {
    // 忽略 = 仅 sessionStorage 本次隐藏，服务端状态不变，刷新重现
    const s = new Set(get().ignored)
    s.add(todoId)
    saveIgnored(s)
    set({ ignored: s })
  },

  async feedback(todoId, text) {
    await api('POST', `/todos/${todoId}/feedback`, { text })
    await get().load()
  },

  async na(todoId, reason) {
    await api('POST', `/todos/${todoId}/na`, { reason })
    await get().load()
  },

  async naConfirm(todoId) {
    await api('POST', `/todos/${todoId}/na-confirm`)
    await get().load()
  },

  async naReject(todoId, comment) {
    await api('POST', `/todos/${todoId}/na-reject`, { comment })
    await get().load()
  },

  async complete(todoId) {
    await api('POST', `/todos/${todoId}/complete`)
    await get().load()
  },
}))

export function startTodoPolling() {
  stopTodoPolling()
  pollTimer = setInterval(() => {
    void useTodoStore.getState().load()
  }, 30_000)
}

export function stopTodoPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}
