// 框架件（不随模式切换而改变布局）：模式切换钮 / 左历史抽屉 / 右待办竖条 / 看板第二屏（全屏翻页）
import { useEffect, useRef, useState } from 'react'
import { useTodoStore } from '@/state/todoStore'
import { useUiStore } from '@/state/uiStore'
import TaskList from './TaskList'
import TodoPanel from './TodoPanel'
import ScreenDashboard from './ScreenDashboard'

/* 模式切换：页面顶部中间品牌标识（瞭望塔页=xin-watchtower / 驾驶舱页=xin-cockpit），点击返回开屏页；
   鼠标移上去展开左右双卡，移入卡片放大并显现竖排文字，点击切换。
   卡片收合以「指针彻底离开标识与两张卡片的联合范围」为准（240ms 缓冲防抖） */
export function ModeToggle({ onHome }: { onHome?: () => void }) {
  const mode = useUiStore((s) => s.mode)
  const setMode = useUiStore((s) => s.setMode)
  const [cardsOpen, setCardsOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const closeTimer = useRef<number | null>(null)
  const posRef = useRef({ x: -9999, y: -9999 })

  const cancelClose = () => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
  }
  useEffect(() => cancelClose, [])

  // 指针是否仍在 V 图标与两张卡片的联合范围（含 12px 缓冲）
  const withinRange = (x: number, y: number) => {
    const root = rootRef.current
    if (!root) return false
    let l = Infinity
    let t = Infinity
    let r = -Infinity
    let b = -Infinity
    root.querySelectorAll<HTMLElement>('.mt-v, .mt-card').forEach((el) => {
      const rc = el.getBoundingClientRect()
      if (rc.left < l) l = rc.left
      if (rc.top < t) t = rc.top
      if (rc.right > r) r = rc.right
      if (rc.bottom > b) b = rc.bottom
    })
    const pad = 12
    return x >= l - pad && x <= r + pad && y >= t - pad && y <= b + pad
  }

  // 卡片展开期间跟踪指针：在联合范围内保持展开，彻底离开后延迟收合
  useEffect(() => {
    if (!cardsOpen) return
    const onMove = (ev: MouseEvent) => {
      posRef.current = { x: ev.clientX, y: ev.clientY }
      if (withinRange(ev.clientX, ev.clientY)) {
        cancelClose()
      } else if (closeTimer.current === null) {
        closeTimer.current = window.setTimeout(() => {
          closeTimer.current = null
          if (!withinRange(posRef.current.x, posRef.current.y)) setCardsOpen(false)
        }, 240)
      }
    }
    document.addEventListener('mousemove', onMove)
    return () => {
      document.removeEventListener('mousemove', onMove)
      cancelClose()
    }
  }, [cardsOpen])

  return (
    <div
      ref={rootRef}
      className={`mode-toggle ${cardsOpen ? 'cards-open' : ''}`}
      role="group"
      aria-label="模式切换"
      onMouseEnter={() => {
        cancelClose()
        setCardsOpen(true)
      }}
    >
      <button type="button" className="mt-v" aria-label="返回开屏页" title="返回开屏页" onClick={onHome}>
        <img className="v-logo tower" src="/assets/xin-watchtower.svg" alt="" aria-hidden="true" />
        <img className="v-logo cockpit" src="/assets/xin-cockpit.svg" alt="" aria-hidden="true" />
      </button>
      <div className="mt-card mt-card--tower" role="button" tabIndex={0} aria-label="切换到 Xin语（瞭望塔）" onClick={() => setMode('tower')}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setMode('tower') } } }>
        <span className="mt-art" />
        <span className="mt-vt">
          <i>攻 · 面向未来</i>
          <b>瞭望塔</b>
        </span>
        <span className="mt-mini-tag">Xin语</span>
        {mode === 'tower' && <span className="mt-cur" aria-hidden="true" />}
      </div>
      <div className="mt-card mt-card--cockpit" role="button" tabIndex={0} aria-label="切换到 Xin台（驾驶舱）" onClick={() => setMode('cockpit')}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setMode('cockpit') } } }>
        <span className="mt-art" />
        <span className="mt-vt">
          <i>守 · 立足当下</i>
          <b>驾驶舱</b>
        </span>
        <span className="mt-mini-tag">Xin台</span>
        {mode === 'cockpit' && <span className="mt-cur" aria-hidden="true" />}
      </div>
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
            <h3>{mode === 'cockpit' ? '我的看板' : '我的看板'}</h3>
          </div>
          <ScreenDashboard />
        </div>
      </section>
    </>
  )
}
