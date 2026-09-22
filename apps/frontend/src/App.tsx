// 根组件：登录态门控 + 双模式层（Xin语⇄Xin台）+ 框架（顶栏/历史/待办/看板）+ 弹窗/Toast 宿主
import { useEffect, useState } from 'react'
import { useAuthStore } from '@/state/authStore'
import { useSessionStore } from '@/state/sessionStore'
import { startTodoPolling, stopTodoPolling, useTodoStore } from '@/state/todoStore'
import { useUiStore } from '@/state/uiStore'
import { runtimeEnv } from '@/config'
import { setToken } from '@/transport/api'
import LoginPage from '@/shell/LoginPage'
import TopBar from '@/shell/TopBar'
import ScreenWork from '@/shell/ScreenWork'
import CockpitHome from '@/shell/CockpitHome'
import Starfield from '@/shell/Starfield'
import SceneModal from '@/shell/SceneModal'
import FillFormModal from '@/shell/FillFormModal'
import TaskDetailModal from '@/shell/TaskDetailModal'
import MeetingPage from '@/shell/MeetingPage'
import ToastHost from '@/primitives/Toast'
import { ModeToggle, HistoryDrawer, TodoRail, KanbanDrawer } from '@/shell/Frame'
import SplashGate from '@/shell/SplashGate'

export default function App() {
  const token = useAuthStore((s) => s.token)
  const ready = useAuthStore((s) => s.ready)
  const mode = useUiStore((s) => s.mode)
  const [splashDone, setSplashDone] = useState(false)
  const historyOpen = useUiStore((s) => s.historyOpen)
  const historyPinned = useUiStore((s) => s.historyPinned)
  const kanbanOpen = useUiStore((s) => s.kanbanOpen)

  useEffect(() => {
    // SSO 回跳：?sso_token= 存登录态并清参（须先于 fetchMe 执行）
    const q = new URLSearchParams(window.location.search)
    const t = q.get('sso_token')
    if (t) {
      setToken(t)
      useAuthStore.setState({ token: t, user: null })
      q.delete('sso_token')
      const rest = q.toString()
      window.history.replaceState({}, '', `${window.location.pathname}${rest ? `?${rest}` : ''}`)
    }
    void useAuthStore.getState().fetchMe()
  }, [])

  useEffect(() => {
    if (!token) return
    startTodoPolling()
    void useSessionStore.getState().loadSessions()
    void useTodoStore.getState().load()
    return () => stopTodoPolling()
  }, [token])

  // 模式/抽屉状态同步到 body class，驱动全局配色与抽屉开合
  useEffect(() => {
    const b = document.body
    b.classList.toggle('mode-tower', mode === 'tower')
    b.classList.toggle('mode-cockpit', mode === 'cockpit')
    b.classList.toggle('mode-qingshan', mode === 'qingshan')
    b.classList.toggle('history-open', historyOpen)
    b.classList.toggle('history-pinned', historyPinned)
    b.classList.toggle('kanban-open', kanbanOpen)
  }, [mode, historyOpen, historyPinned, kanbanOpen])

  if (!ready) return <section className="layer layer-tower"><div className="bg" /></section>
  // 青山知识库层（登录态无关都要渲染：未登录也可能从开屏进入）：自下向上推进入场，
  // 内嵌知识库平台首页（KB_URL，运行时/构建期配置）；未配置或加载期间露出青绿山水加载幕
  const qingshanLayer = (
    <section className="layer layer-qingshan">
      <div className="bg" />
      {mode === 'qingshan' && runtimeEnv.KB_URL && (
        <iframe className="qs-frame" src={runtimeEnv.KB_URL} title="青山知识库" />
      )}
    </section>
  )
  if (!token) {
    return (
      <>
        <section className="layer layer-tower">
          <div className="bg" />
        </section>
        {qingshanLayer}
        <LoginPage />
        {!splashDone && <SplashGate onDone={() => setSplashDone(true)} />}
      </>
    )
  }
  return (
    <>
      {/* Xin台 · 守（日） */}
      <section className="layer layer-cockpit">
        <div className="bg" />
        <CockpitHome />
      </section>
      {/* Xin语 · 攻（夜） */}
      <section className="layer layer-tower">
        <div className="bg" />
        <Starfield visible={mode === 'tower' && !kanbanOpen} />
        <ScreenWork />
      </section>
      {/* 青山知识库（青绿山水·第三页面）：点击开屏题字后自下向上推进入场，内嵌知识库平台首页 */}
      {qingshanLayer}
      {/* 框架（不随模式切换而改变布局） */}
      <TopBar />
      <ModeToggle onHome={() => setSplashDone(false)} />
      <HistoryDrawer />
      <TodoRail />
      <KanbanDrawer />
      <SceneModal />
      <FillFormModal />
      <TaskDetailModal />
      <MeetingPage />
      <ToastHost />
      {/* 开屏双门：点击进入 Xin语 / Xin台 */}
      {!splashDone && <SplashGate onDone={() => setSplashDone(true)} />}
    </>
  )
}
