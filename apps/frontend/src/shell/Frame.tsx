// 框架件（不随模式切换而改变布局）：模式切换钮 / 左历史抽屉 / 右待办竖条 / 底部看板抽屉
import { useTodoStore } from '@/state/todoStore'
import { useUiStore } from '@/state/uiStore'
import HistoryRail from './HistoryRail'
import TodoPanel from './TodoPanel'
import ScreenDashboard from './ScreenDashboard'

/* 模式切换：瞭望塔恒前、驾驶舱恒后，选中项由纵深推至前方 */
export function ModeToggle() {
  const mode = useUiStore((s) => s.mode)
  const setMode = useUiStore((s) => s.setMode)
  return (
    <div className="mode-toggle">
      <button type="button" className={`mt-btn ${mode === 'tower' ? 'on' : ''}`} onClick={() => setMode('tower')}>
        瞭望塔
      </button>
      <button type="button" className={`mt-btn ${mode === 'cockpit' ? 'on' : ''}`} onClick={() => setMode('cockpit')}>
        驾驶舱
      </button>
    </div>
  )
}

/* 框架一 · 历史对话抽屉 */
export function HistoryDrawer() {
  const toggleHistory = useUiStore((s) => s.toggleHistory)
  return (
    <>
      <button className="h-toggle" onClick={toggleHistory}>
        <span className="ic">›</span>
        <span className="vt">历史对话</span>
      </button>
      <aside className="h-drawer">
        <div className="h-title">History · 历史对话</div>
        <HistoryRail />
      </aside>
    </>
  )
}

/* 框架二 · 待办竖条（悬停/点击展开） */
export function TodoRail() {
  const count = useTodoStore((s) => s.items.filter((t) => !s.ignored.has(t.todo_id)).length)
  return (
    <aside className="r-rail" aria-label={`待我处理，${count} 项`}>
      <span className="r-liquid-shine" aria-hidden="true" />
      <span className="badge">{count}</span>
      <span className="label">待&nbsp;我&nbsp;处&nbsp;理</span>
      <div className="r-pop" onClick={(e) => e.stopPropagation()}>
        <TodoPanel />
      </div>
    </aside>
  )
}

/* 框架三 · 底部看板抽屉（承载 Dashboard 数据） */
export function KanbanDrawer() {
  const mode = useUiStore((s) => s.mode)
  const toggleKanban = useUiStore((s) => s.toggleKanban)
  return (
    <>
      <button className="k-toggle" onClick={toggleKanban}>
        <span className="arrow">↓</span>
        <span>下滑查看看板</span>
      </button>
      <div className="k-drawer">
        <div className="k-inner">
          <div className="k-head">
            <h3>{mode === 'cockpit' ? '经营看板' : '研究看板'}</h3>
            <button className="close" onClick={toggleKanban} title="关闭">
              ×
            </button>
          </div>
          <ScreenDashboard />
        </div>
      </div>
    </>
  )
}
