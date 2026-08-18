// 屏1 工作台：恒三列布局（历史 | 中央 | 待办）
// 中央区：hero 大问数框（默认）↔ ChatPanel 对话视图
// 切到屏2 时隐藏历史/待办栏，回屏1 恢复；收拢悬浮钮（折叠态本地 state，刷新复位）
import { useState } from 'react'
import { useTodoStore } from '@/state/todoStore'
import { useUiStore } from '@/state/uiStore'
import HistoryRail from './HistoryRail'
import ChatPanel from './ChatPanel'
import TodoPanel from './TodoPanel'
import HeroHome from './HeroHome'

export default function ScreenWork() {
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  const todoCount = useTodoStore((s) => s.items.filter((t) => !s.ignored.has(t.todo_id)).length)
  const activeScreen = useUiStore((s) => s.activeScreen)
  const workView = useUiStore((s) => s.workView)
  const sideVisible = activeScreen === 'work' // 屏2 不显示历史会话和待办

  return (
    <section className="screen" id="screen-work">
      <div className={`work-body ${workView === 'hero' ? 'hero-mode' : ''}`}>
        {leftCollapsed && sideVisible && (
          <div className="rail-float left" onClick={() => setLeftCollapsed(false)}>
            <span className="lbl">历史会话</span>
          </div>
        )}
        {sideVisible && (
          <div className={`glass-col col-history ${leftCollapsed ? 'collapsed' : ''}`}>
            <HistoryRail onCollapse={() => setLeftCollapsed(true)} />
          </div>
        )}
        <div className={`glass-col col-chat ${workView === 'hero' ? 'transparent' : ''}`}>
          {workView === 'hero' ? <HeroHome /> : <ChatPanel />}
        </div>
        {sideVisible && (
          <div className={`glass-col col-todo ${rightCollapsed ? 'collapsed' : ''}`}>
            <TodoPanel onCollapse={() => setRightCollapsed(true)} />
          </div>
        )}
        {rightCollapsed && sideVisible && (
          <div className="rail-float right" onClick={() => setRightCollapsed(false)}>
            <span className="lbl">待办</span>
            {todoCount > 0 && <span className="count">{todoCount}</span>}
          </div>
        )}
      </div>
    </section>
  )
}
