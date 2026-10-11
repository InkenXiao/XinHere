// 逆回购行情提醒：SSE repo/alerted → 浏览器级系统通知（最小化也弹）+ 会话内提醒列表；
// /repo-alerts 日志页数据（连接状态 + 接入流水）走 5s 轮询（引用计数，仅页面挂载期间拉）
import { create } from 'zustand'
import { api } from '../api'
import { useUiStore } from '../uiShim'

export interface RepoAlertContent {
  eventId?: string
  productCode?: string
  title?: string
  body?: string
  [key: string]: unknown
}

export interface RepoStreamStatus {
  enabled: boolean
  state: 'stopped' | 'connecting' | 'online' | 'disconnected' | string
  url?: string
  connected_at?: string | null
  last_heartbeat_at?: string | null
  last_alert_at?: string | null
  last_error?: string | null
  alert_count?: number
  heartbeat_count?: number
  reconnect_count?: number
}

export interface RepoLogItem {
  time: string
  kind: 'connect' | 'heartbeat' | 'alert' | 'disconnect' | 'error' | string
  message: string
  data?: Record<string, unknown> | null
}

interface RepoAlertState {
  status: RepoStreamStatus | null
  log: RepoLogItem[]
  alerts: RepoAlertContent[] // 本会话收到的提醒（新在前）
  notifPermission: NotificationPermission | 'unsupported'
  refresh: () => Promise<void>
  requestNotifPermission: () => Promise<void>
}

let pollTimer: ReturnType<typeof setInterval> | null = null
let pollRefs = 0

/** SSE repo/alerted 帧 → 系统通知 + 会话内列表；由 todoStore 触达流 onEvent 调用 */
export function repoAlertReceived(content: Record<string, unknown>) {
  const alert = content as RepoAlertContent
  useRepoAlertStore.setState((s) => ({ alerts: [alert, ...s.alerts].slice(0, 50) }))
  fireNotification(alert)
  useUiStore.getState().toast(`${alert.title || '逆回购利率提醒'}：${alert.body || ''}`)
}

function fireNotification(alert: RepoAlertContent) {
  if (typeof Notification === 'undefined') return
  if (Notification.permission !== 'granted') {
    if (Notification.permission === 'default') void useRepoAlertStore.getState().requestNotifPermission()
    return
  }
  new Notification(alert.title || '逆回购利率提醒', {
    body: alert.body || '',
    tag: alert.eventId || alert.productCode, // 同事件去重，避免重复弹
  })
}

export const useRepoAlertStore = create<RepoAlertState>((set) => ({
  status: null,
  log: [],
  alerts: [],
  notifPermission: typeof Notification === 'undefined' ? 'unsupported' : Notification.permission,

  async refresh() {
    const [st, lg] = await Promise.all([
      api<RepoStreamStatus>('GET', '/repo-alerts/status', undefined, { silent: true }),
      api<{ items: RepoLogItem[] }>('GET', '/repo-alerts/log?limit=300', undefined, { silent: true }),
    ])
    set({
      status: st.data,
      log: [...(lg.data.items ?? [])].reverse(), // 新在前
      notifPermission: typeof Notification === 'undefined' ? 'unsupported' : Notification.permission,
    })
  },

  async requestNotifPermission() {
    if (typeof Notification === 'undefined') return
    try {
      set({ notifPermission: await Notification.requestPermission() })
    } catch {
      /* 浏览器策略拒绝：保持 default，页面按钮可再试 */
    }
  },
}))

/** 日志页轮询（引用计数：页面挂载启动，卸载停） */
export function startRepoAlertPolling() {
  pollRefs += 1
  if (pollTimer) return
  void useRepoAlertStore.getState().refresh()
  pollTimer = setInterval(() => void useRepoAlertStore.getState().refresh(), 5000)
}

export function stopRepoAlertPolling() {
  pollRefs = Math.max(0, pollRefs - 1)
  if (pollRefs === 0 && pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}
