// 会话：列表/当前会话/事件流（Assembler 输出）/发送/断线重连
import { create } from 'zustand'
import { api } from '@/transport/api'
import { streamChat, resumeEvents } from '@/transport/sse'
import { ConversationAssembler, type AssemblerSnapshot } from '@/registry/ConversationAssembler'
import { componentDefs } from '@/registry/manifest'
import type { PlatformEvent, SessionHeader, SessionListItem } from '@/types'
import { sleep } from '@/utils'
import { useUiStore } from './uiStore'
import { useTodoStore } from './todoStore'

interface SessionState {
  sessions: SessionListItem[]
  current: SessionHeader | null
  snap: AssemblerSnapshot // Assembler 输出快照（渲染用）
  sending: boolean
  connStatus: 'open' | 'reconnecting' | 'closed'
  asmError: string | null // fail-closed 装配错误
  loadSessions: () => Promise<void>
  openSession: (id: string) => Promise<void>
  newSession: () => Promise<void>
  send: (text: string, kbIds?: string[], model?: string) => Promise<void>
  cancel: () => Promise<void>
  componentEmit: (componentId: string) => {
    update: (draft: Record<string, unknown>) => Promise<void>
    submit: (action: 'submit' | 'cancel', values?: Record<string, unknown>) => Promise<void>
  }
}

let assembler = new ConversationAssembler(componentDefs)
let abortCtl: AbortController | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null

function syncSnap(set: (p: Partial<SessionState>) => void) {
  set({ snap: assembler.snapshot() })
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: [],
  current: null,
  snap: assembler.snapshot(),
  sending: false,
  connStatus: 'open',
  asmError: null,

  async loadSessions() {
    const r = await api<{ items: SessionListItem[]; total: number }>('GET', '/sessions?limit=100')
    set({ sessions: r.items })
  },

  async openSession(id) {
    reconnectStop()
    abortCtl?.abort()
    assembler = new ConversationAssembler(componentDefs)
    set({ asmError: null, sending: false, connStatus: 'open' })
    syncSnap(set)
    const detail = await api<SessionHeader>('GET', `/sessions/${id}`)
    set({ current: detail })
    // 回放：GET events JSON → 同一 Assembler 重建
    const evts = await api<{ items: PlatformEvent[]; has_more: boolean }>('GET', `/sessions/${id}/events?limit=500`)
    feedMany(evts.items, set)
  },

  async newSession() {
    reconnectStop()
    abortCtl?.abort()
    const s = await api<SessionHeader>('POST', '/sessions', {})
    assembler = new ConversationAssembler(componentDefs)
    set({ current: s, asmError: null, sending: false })
    syncSnap(set)
    await get().loadSessions()
  },

  async send(text, kbIds, model) {
    const cur = get().current
    if (!cur || get().sending) return
    set({ sending: true, asmError: null })
    useUiStore.getState().setExecDone(false)
    abortCtl = new AbortController()
    const sid = cur.session_id
    // 模型参数：调用方显式指定优先，否则取全局当前选择（值对 value，如 LLM）
    const modelParam = model ?? useUiStore.getState().modelValue
    let sawEvents = false
    const handlers = {
      onEvent: (evt: PlatformEvent) => {
        sawEvents = true
        if (evt.type === 'todo/changed') {
          // 待办实时刷新（ignorable 事件）
          void useTodoStore.getState().load()
        }
        feedOne(evt, set)
      },
      onStatus: (s: 'open' | 'reconnecting' | 'closed') => {
        set({ connStatus: s })
      },
    }
    try {
      await streamChat(sid, { message: text, kb_ids: kbIds, model: modelParam }, handlers, abortCtl.signal)
    } catch (e) {
      if ((e as Error).name === 'AbortError') return
      // 断线：指数退避重连（GET events SSE 续流，after_seq），不清屏
      set({ connStatus: 'reconnecting' })
      let delay = 800
      let lastSeq = assembler.getMaxSeq()
      let lastEventId: string | null = null
      for (let attempt = 0; attempt < 8 && !abortCtl.signal.aborted; attempt++) {
        await sleep(delay)
        try {
          await resumeEvents(sid, lastSeq, lastEventId, {
            onEvent: (evt) => {
              lastSeq = Math.max(lastSeq, evt.seq)
              lastEventId = `${sid}:${evt.seq}`
              feedOne(evt, set)
            },
            onStatus: handlers.onStatus,
          }, abortCtl.signal)
          break
        } catch (err) {
          if ((err as Error).name === 'AbortError') break
          delay = Math.min(delay * 2, 8000)
        }
      }
      set({ connStatus: 'open' })
    } finally {
      set({ sending: false, connStatus: 'open' })
      if (!sawEvents) {
        useUiStore.getState().toast('未收到回复，请重试', 'err')
      }
    }
  },

  async cancel() {
    const cur = get().current
    if (!cur) return
    abortCtl?.abort()
    try {
      await api('POST', `/sessions/${cur.session_id}/cancel`)
    } catch {
      /* 忽略 */
    }
    set({ sending: false })
  },

  componentEmit(componentId) {
    const sid = get().current?.session_id
    return {
      update: async (draft) => {
        if (!sid) return
        await api('POST', `/sessions/${sid}/components/${componentId}/update`, { draft })
      },
      submit: async (action, values) => {
        if (!sid) return
        const node = assembler.findComponent(componentId)
        const interruptId = node?.base.interrupt_id ?? ''
        await api('POST', `/sessions/${sid}/components/${componentId}/submit`, {
          action,
          values,
          interrupt_id: interruptId,
        })
        // submit 后 resume 产生的新事件走事件日志；chat SSE 已随 interrupt 挂起关闭，
        // 这里续流监听（GET events SSE），把 submit/工具结果/报告进度/文件卡等事件刷进 UI。
        // turn/end 后留 10s 宽限（异步报告生成的 file/record 晚于 turn 结束），到时断开。
        const ctl = new AbortController()
        let endTimer: ReturnType<typeof setTimeout> | null = null
        const lastSeq = assembler.getMaxSeq()
        resumeEvents(sid, lastSeq, `${sid}:${lastSeq}`, {
          onEvent: (evt) => {
            feedOne(evt, set)
            if (evt.type === 'turn/end' && !endTimer) {
              endTimer = setTimeout(() => ctl.abort(), 10_000)
            }
          },
          onStatus: (s) => set({ connStatus: s }),
        }, ctl.signal).catch((e) => {
          if ((e as Error).name !== 'AbortError') {
            useUiStore.getState().toast('事件同步中断，请刷新查看结果', 'err')
          }
        })
      },
    }
  },
}))

function feedOne(evt: PlatformEvent, set: (p: Partial<SessionState>) => void) {
  try {
    assembler.feed(evt)
    // 触发执行态：component/request 出现即进入执行视角
    if (evt.type === 'component/request') {
      useUiStore.getState().setExecuting(true)
    }
    if (evt.type === 'turn/end') {
      useUiStore.getState().setExecDone(true)
    }
    syncSnap(set)
  } catch (e) {
    set({ asmError: (e as Error).message })
    useUiStore.getState().toast((e as Error).message, 'err')
  }
}

function feedMany(events: PlatformEvent[], set: (p: Partial<SessionState>) => void) {
  try {
    assembler.feedAll(events)
    syncSnap(set)
  } catch (e) {
    set({ asmError: (e as Error).message })
  }
}

function reconnectStop() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}
