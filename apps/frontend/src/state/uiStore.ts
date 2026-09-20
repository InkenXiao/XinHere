// UI 状态：主题、执行态开关、当前激活场景组件（去填报）、Toast
import { create } from 'zustand'
import type { TaskRecordItem, TodoScene } from '@/types'

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
  mode: 'tower' | 'cockpit' | 'qingshan' // 三模式：Xin语（夜·问答）⇄ Xin台（日·业务）⇄ 青山知识库（青绿山水）
  historyOpen: boolean // 框架一：左侧历史对话抽屉
  historyPinned: boolean // 历史抽屉固定：pinned 时常驻展开、失焦不收起
  kanbanOpen: boolean // 框架三：底部看板抽屉
  todoPinned: boolean // 待办竖条固定：pinned 时常驻展开
  switching: boolean // 模式切换雾式转场窗口
  activeScreen: 'work' | 'dash' // deprecated: 抽屉模型下不再使用（原 TopBar 滚动跟踪）
  workView: 'hero' | 'chat' // Xin语层内视图：hero 大问数框（默认）↔ 会话
  executing: boolean // 执行态开关：中央区 ChatPanel ↔ ExecutionView
  execDone: boolean
  sceneTarget: SceneTarget | null // 待办「去填报/去审批」打开的场景组件
  fillTarget: { assignmentId: number; templateId: number } | null // 待办「去填报」打开的 XuanPu 填报弹窗
  meetingOpen: boolean // 实时会议页开关（录音由 meetingStore 持有，关闭页面不影响后台运行）
  execDetail: TaskRecordItem | null // 历史任务「执行记录」详情弹窗目标
  toasts: ToastItem[]
  setMode: (v: 'tower' | 'cockpit' | 'qingshan') => void
  toggleHistory: () => void
  setHistoryOpen: (v: boolean) => void
  toggleHistoryPin: () => void
  toggleTodoPin: () => void
  toggleKanban: () => void
  setActiveScreen: (v: 'work' | 'dash') => void
  setWorkView: (v: 'hero' | 'chat') => void
  setExecuting: (v: boolean) => void
  setExecDone: (v: boolean) => void
  openScene: (t: SceneTarget | null) => void
  openFill: (t: { assignmentId: number; templateId: number } | null) => void
  setMeetingOpen: (v: boolean) => void
  openExecDetail: (t: TaskRecordItem | null) => void
  toast: (text: string, kind?: 'info' | 'err') => void
  dismissToast: (id: number) => void
}

let toastSeq = 0

export const useUiStore = create<UiState>((set) => ({
  theme: 'dark',
  mode: 'tower',
  historyOpen: false,
  historyPinned: localStorage.getItem('xinhere.ui.historyPinned') === '1',
  kanbanOpen: false,
  todoPinned: localStorage.getItem('xinhere.ui.todoPinned') === '1',
  switching: false,
  activeScreen: 'work',
  workView: 'hero',
  executing: false,
  execDone: false,
  sceneTarget: null,
  fillTarget: null,
  meetingOpen: false,
  execDetail: null,
  toasts: [],
  setMode: (v) => {
    set({ mode: v, switching: true })
    setTimeout(() => set({ switching: false }), 1150)
  },
  toggleHistory: () => set((s) => ({ historyOpen: !s.historyOpen })),
  setHistoryOpen: (v) => set({ historyOpen: v }),
  toggleHistoryPin: () =>
    set((s) => {
      const historyPinned = !s.historyPinned
      localStorage.setItem('xinhere.ui.historyPinned', historyPinned ? '1' : '0')
      return { historyPinned }
    }),
  toggleTodoPin: () =>
    set((s) => {
      const todoPinned = !s.todoPinned
      localStorage.setItem('xinhere.ui.todoPinned', todoPinned ? '1' : '0')
      return { todoPinned }
    }),
  toggleKanban: () => set((s) => ({ kanbanOpen: !s.kanbanOpen })),
  setActiveScreen: (v) => set({ activeScreen: v }),
  setWorkView: (v) => set({ workView: v }),
  setExecuting: (v) => set({ executing: v }),
  setExecDone: (v) => set({ execDone: v }),
  openScene: (t) => set({ sceneTarget: t }),
  openFill: (t) => set({ fillTarget: t }),
  setMeetingOpen: (v) => set({ meetingOpen: v }),
  openExecDetail: (t) => set({ execDetail: t }),
  toast: (text, kind = 'info') => {
    const id = ++toastSeq
    set((s) => ({ toasts: [...s.toasts, { id, text, kind }] }))
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
    }, 3200)
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))
