// 框架件（不随模式切换而改变布局）：模式切换钮 / 左历史抽屉 / 右待办竖条 / 看板第二屏（全屏翻页）
import { useTodoStore } from '@/state/todoStore'
import { useUiStore } from '@/state/uiStore'
import TaskList from './TaskList'
import TodoPanel from './TodoPanel'
import ScreenDashboard from './ScreenDashboard'

/* 模式切换：点击 Xin语 前推、点击 Xin台 后退，选中项由纵深推至前方 */
export function ModeToggle() {
  const mode = useUiStore((s) => s.mode)
  const setMode = useUiStore((s) => s.setMode)
  return (
    <div className="mode-toggle">
      <button type="button" className={`mt-btn ${mode === 'tower' ? 'on' : ''}`} onClick={() => setMode('tower')}>
        Xin语
      </button>
      <button type="button" className={`mt-btn ${mode === 'cockpit' ? 'on' : ''}`} onClick={() => setMode('cockpit')}>
        Xin台
      </button>
    </div>
  )
}

/* 图钉图标：pinned=实心 / 未固定=空心+斜杠 */
function PinIcon({ pinned }: { pinned: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
      {pinned ? (
        <path fill="currentColor" d="M16 9V4h1V2H7v2h1v5l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z" />
      ) : (
        <>
          <path
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinejoin="round"
            d="M16 9V4h1V2H7v2h1v5l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z"
          />
          <path stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" d="M4.5 4.5l15 15" />
        </>
      )}
    </svg>
  )
}

/* 框架一 · 历史任务抽屉：左缘热区唤起 + pin 固定常驻（未固定时移出抽屉自动收起）
   内容按模式加载：Xin语=AI 对话记录 / Xin台=AI 能力执行记录 */
export function HistoryDrawer() {
  const historyPinned = useUiStore((s) => s.historyPinned)
  const setHistoryOpen = useUiStore((s) => s.setHistoryOpen)
  const toggleHistoryPin = useUiStore((s) => s.toggleHistoryPin)
  return (
    <>
      <div className="h-hotzone" onClick={() => setHistoryOpen(true)} title="历史任务" aria-hidden="true" />
      <aside
        className="h-drawer"
        onMouseLeave={() => {
          if (!historyPinned) setHistoryOpen(false)
        }}
      >
        <div className="h-title">
          <span>History · 历史任务</span>
          <button
            type="button"
            className={`h-pin ${historyPinned ? 'on' : ''}`}
            onClick={toggleHistoryPin}
            title={historyPinned ? '取消固定' : '固定'}
          >
            <PinIcon pinned={historyPinned} />
          </button>
        </div>
        <TaskList />
      </aside>
    </>
  )
}

/* 框架二 · 待办竖条（悬停展开 / pin 固定常驻展开） */
export function TodoRail() {
  const count = useTodoStore((s) => s.items.filter((t) => !s.ignored.has(t.todo_id)).length)
  const todoPinned = useUiStore((s) => s.todoPinned)
  const toggleTodoPin = useUiStore((s) => s.toggleTodoPin)
  return (
    <aside className={`r-rail ${todoPinned ? 'open' : ''}`} aria-label={`待我处理，${count} 项`}>
      <span className="r-liquid-shine" aria-hidden="true" />
      <span className="badge">{count}</span>
      <span className="label">待&nbsp;我&nbsp;处&nbsp;理</span>
      <div className="r-pop" onClick={(e) => e.stopPropagation()}>
        <div className="r-pop-head">
          <button
            type="button"
            className={`h-pin ${todoPinned ? 'on' : ''}`}
            onClick={toggleTodoPin}
            title={todoPinned ? '取消固定' : '固定'}
          >
            <PinIcon pinned={todoPinned} />
          </button>
        </div>
        <TodoPanel />
      </div>
    </aside>
  )
}

/* 框架三 · 看板第二屏：全屏翻页（首屏不卸载、保留现场；「返回」翻回第一屏） */
export function KanbanDrawer() {
  const mode = useUiStore((s) => s.mode)
  const kanbanOpen = useUiStore((s) => s.kanbanOpen)
  const toggleKanban = useUiStore((s) => s.toggleKanban)
  return (
    <>
      <button className="k-toggle" onClick={toggleKanban}>
        <span className="arrow">↓</span>
        <span>下滑查看看板</span>
      </button>
      <section className="k-screen" aria-hidden={!kanbanOpen}>
        <div className="k-inner">
          <div className="k-head">
            <button className="back" onClick={toggleKanban}>
              <span className="arrow">↑</span>
              <span>返回</span>
            </button>
            <h3>{mode === 'cockpit' ? '经营看板' : '研究看板'}</h3>
          </div>
          <ScreenDashboard />
        </div>
      </section>
    </>
  )
}
