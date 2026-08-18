// UI 状态：主题、执行态开关、当前激活场景组件（去填报）、Toast
import { create } from 'zustand'
import type { TodoScene } from '@/types'

export type Theme = 'dark' | 'light'

const THEME_KEY = 'xinhere.theme'

function initTheme(): Theme {
  try {
    return localStorage.getItem(THEME_KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

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
  theme: Theme
  activeScreen: 'work' | 'dash' // 当前所在屏（TopBar 滚动跟踪写入；屏1 据此隐藏历史/待办栏）
  workView: 'hero' | 'chat' // 屏1 视图：hero 大问数框（默认）↔ 三栏工作台
  executing: boolean // 执行态开关：中央区 ChatPanel ↔ ExecutionView
  execDone: boolean
  sceneTarget: SceneTarget | null // 待办「去填报/去审批」打开的场景组件
  templateSkill: string | null // 模版选择 Modal 当前技能 key（null=关闭）
  skillSettingsOpen: boolean // 技能设置 Modal
  toasts: ToastItem[]
  toggleTheme: () => void
  setActiveScreen: (v: 'work' | 'dash') => void
  setWorkView: (v: 'hero' | 'chat') => void
  setExecuting: (v: boolean) => void
  setExecDone: (v: boolean) => void
  openScene: (t: SceneTarget | null) => void
  openTemplateModal: (skillKey: string) => void
  closeTemplateModal: () => void
  setSkillSettingsOpen: (v: boolean) => void
  toast: (text: string, kind?: 'info' | 'err') => void
  dismissToast: (id: number) => void
}

let toastSeq = 0

export const useUiStore = create<UiState>((set) => ({
  theme: initTheme(),
  activeScreen: 'work',
  workView: 'hero',
  executing: false,
  execDone: false,
  sceneTarget: null,
  templateSkill: null,
  skillSettingsOpen: false,
  toasts: [],
  toggleTheme: () =>
    set((s) => {
      const next: Theme = s.theme === 'dark' ? 'light' : 'dark'
      try {
        localStorage.setItem(THEME_KEY, next)
      } catch {
        /* 忽略持久化失败 */
      }
      return { theme: next }
    }),
  setActiveScreen: (v) => set({ activeScreen: v }),
  setWorkView: (v) => set({ workView: v }),
  setExecuting: (v) => set({ executing: v }),
  setExecDone: (v) => set({ execDone: v }),
  openScene: (t) => set({ sceneTarget: t }),
  openTemplateModal: (skillKey) => set({ templateSkill: skillKey }),
  closeTemplateModal: () => set({ templateSkill: null }),
  setSkillSettingsOpen: (v) => set({ skillSettingsOpen: v }),
  toast: (text, kind = 'info') => {
    const id = ++toastSeq
    set((s) => ({ toasts: [...s.toasts, { id, text, kind }] }))
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
    }, 3200)
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))
