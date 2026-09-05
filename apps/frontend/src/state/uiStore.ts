// UI 状态：主题、执行态开关、当前激活场景组件（去填报）、Toast
import { create } from 'zustand'
import type { TodoScene } from '@/types'

export interface SceneTarget {
  scene: TodoScene
  ref: Record<string, unknown>
  title: string
  mode?: 'fill' | 'review'
}

interface ToastItem {
  id: number
  text: string
  kind: 'info' | 'err'
}

interface UiState {
  theme: 'dark'
  mode: 'tower' | 'cockpit' // 双模式：瞭望塔（夜·问答）⇄ 驾驶舱（日·业务）
  historyOpen: boolean // 框架一：左侧历史对话抽屉
  kanbanOpen: boolean // 框架三：底部看板抽屉
  switching: boolean // 模式切换雾式转场窗口
  activeScreen: 'work' | 'dash' // deprecated: 抽屉模型下不再使用（原 TopBar 滚动跟踪）
  workView: 'hero' | 'chat' // 瞭望塔层内视图：hero 大问数框（默认）↔ 会话
  executing: boolean // 执行态开关：中央区 ChatPanel ↔ ExecutionView
  execDone: boolean
  sceneTarget: SceneTarget | null // 待办「去填报/去审批」打开的场景组件
  toasts: ToastItem[]
  setMode: (v: 'tower' | 'cockpit') => void
  toggleHistory: () => void
  toggleKanban: () => void
  setActiveScreen: (v: 'work' | 'dash') => void
  setWorkView: (v: 'hero' | 'chat') => void
  setExecuting: (v: boolean) => void
  setExecDone: (v: boolean) => void
  openScene: (t: SceneTarget | null) => void
  toast: (text: string, kind?: 'info' | 'err') => void
  dismissToast: (id: number) => void
}

let toastSeq = 0

export const useUiStore = create<UiState>((set) => ({
  theme: 'dark',
  mode: 'tower',
  historyOpen: false,
  kanbanOpen: false,
  switching: false,
  activeScreen: 'work',
  workView: 'hero',
  executing: false,
  execDone: false,
  sceneTarget: null,
  toasts: [],
  setMode: (v) => {
    set({ mode: v, switching: true })
    setTimeout(() => set({ switching: false }), 1150)
  },
  toggleHistory: () => set((s) => ({ historyOpen: !s.historyOpen })),
  toggleKanban: () => set((s) => ({ kanbanOpen: !s.kanbanOpen })),
  setActiveScreen: (v) => set({ activeScreen: v }),
  setWorkView: (v) => set({ workView: v }),
  setExecuting: (v) => set({ executing: v }),
  setExecDone: (v) => set({ execDone: v }),
  openScene: (t) => set({ sceneTarget: t }),
  toast: (text, kind = 'info') => {
    const id = ++toastSeq
    set((s) => ({ toasts: [...s.toasts, { id, text, kind }] }))
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
    }, 3200)
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))
