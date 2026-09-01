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
  activeScreen: 'work' | 'dash' // 当前所在屏（TopBar 滚动跟踪写入；屏1 据此隐藏历史/待办栏）
  workView: 'hero' | 'chat' // 屏1 视图：hero 大问数框（默认）↔ 三栏工作台
  executing: boolean // 执行态开关：中央区 ChatPanel ↔ ExecutionView
  execDone: boolean
  sceneTarget: SceneTarget | null // 待办「去填报/去审批」打开的场景组件
  toasts: ToastItem[]
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
  activeScreen: 'work',
  workView: 'hero',
  executing: false,
  execDone: false,
  sceneTarget: null,
  toasts: [],
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
