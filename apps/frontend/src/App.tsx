// 根组件：登录态门控 + 两屏 + 弹窗/Toast 宿主
import { useEffect } from 'react'
import { useAuthStore } from '@/state/authStore'
import { useSessionStore } from '@/state/sessionStore'
import { startTodoPolling, stopTodoPolling, useTodoStore } from '@/state/todoStore'
import LoginPage from '@/shell/LoginPage'
import TopBar from '@/shell/TopBar'
import ScreenWork from '@/shell/ScreenWork'
import ScreenDashboard from '@/shell/ScreenDashboard'
import SceneModal from '@/shell/SceneModal'
import TemplateModal from '@/shell/TemplateModal'
import SkillSettingsModal from '@/shell/SkillSettingsModal'
import ToastHost from '@/primitives/Toast'

export default function App() {
  const token = useAuthStore((s) => s.token)
  const ready = useAuthStore((s) => s.ready)

  useEffect(() => {
    void useAuthStore.getState().fetchMe()
  }, [])

  useEffect(() => {
    if (!token) return
    startTodoPolling()
    void useSessionStore.getState().loadSessions()
    void useTodoStore.getState().load()
    return () => stopTodoPolling()
  }, [token])

  if (!ready) return <div className="app-bg" />
  if (!token) {
    return (
      <>
        <div className="app-bg" />
        <LoginPage />
      </>
    )
  }
  return (
    <>
      <div className="app-bg" />
      <TopBar />
      <div className="screens">
        <ScreenWork />
        <ScreenDashboard />
      </div>
      <SceneModal />
      <TemplateModal />
      <SkillSettingsModal />
      <ToastHost />
    </>
  )
}
