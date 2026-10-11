import { useRef } from 'react'
import type { TemplateTab } from '@contracts/events'

export function TemplateTabs({ tabs, active, onChange, layout = 'steps', enhancedScroll = false, prominentScrollbar = false }: {
  tabs: TemplateTab[]
  active: string
  onChange: (id: string) => void
  layout?: 'steps' | 'tabs'
  enhancedScroll?: boolean
  prominentScrollbar?: boolean
}) {
  const tabsRef = useRef<HTMLElement>(null)

  if (layout === 'tabs') {
    const tabList = (
      <nav
        ref={tabsRef}
        aria-label="表单分页"
        onWheel={enhancedScroll ? (event) => {
          if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return
          event.preventDefault()
          tabsRef.current?.scrollBy({ left: event.deltaY, behavior: 'auto' })
        } : undefined}
        className={`flex min-w-0 flex-1 gap-2 overflow-x-auto ${enhancedScroll || prominentScrollbar ? 'pb-3! [scrollbar-color:#38bdf8_#e2e8f0] [scrollbar-width:auto] [&::-webkit-scrollbar]:h-2.5 [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-slate-200 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:border-2 [&::-webkit-scrollbar-thumb]:border-solid [&::-webkit-scrollbar-thumb]:border-slate-200 [&::-webkit-scrollbar-thumb]:bg-sky-400 hover:[&::-webkit-scrollbar-thumb]:bg-sky-500' : 'pb-2!'}`}
      >
        {tabs.map((tab, index) => {
          const selected = tab.id === active
          return (
            <button
              key={tab.id}
              type="button"
              title={tab.source_file_name}
              aria-current={selected ? 'page' : undefined}
              onClick={() => onChange(tab.id)}
              className={`flex shrink-0 items-center gap-2 rounded-full border px-4! py-2.5! text-base font-semibold transition-colors ${selected ? 'border-sky-600! bg-sky-600! text-white! shadow-sm' : 'border-slate-200! bg-white! text-slate-700! hover:border-sky-300! hover:bg-sky-50!'}`}
            >
              <span className="text-xs opacity-70">{String(index + 1).padStart(2, '0')}</span>
              {tab.name}
            </button>
          )
        })}
      </nav>
    )
    if (!enhancedScroll) return tabList
    const scroll = (direction: -1 | 1) => tabsRef.current?.scrollBy({ left: direction * Math.max(320, tabsRef.current.clientWidth * 0.7), behavior: 'auto' })
    return (
      <div className="flex min-w-0 items-center gap-2">
        <button type="button" aria-label="向左查看更多分页" title="向左查看更多" onClick={() => scroll(-1)} className="grid size-9 shrink-0 place-items-center rounded-full border border-slate-300 bg-white text-lg font-semibold text-sky-700 shadow-sm transition-colors hover:border-sky-400 hover:bg-sky-50">‹</button>
        {tabList}
        <button type="button" aria-label="向右查看更多分页" title="向右查看更多" onClick={() => scroll(1)} className="grid size-9 shrink-0 place-items-center rounded-full border border-slate-300 bg-white text-lg font-semibold text-sky-700 shadow-sm transition-colors hover:border-sky-400 hover:bg-sky-50">›</button>
      </div>
    )
  }

  return (
    <nav aria-label="填报分页" className="flex gap-2 overflow-x-auto pb-1! lg:flex-col lg:overflow-visible lg:pb-0!">
      {tabs.map((tab, index) => {
        const selected = tab.id === active
        return (
          <button
            key={tab.id}
            type="button"
            title={tab.source_file_name}
            aria-current={selected ? 'step' : undefined}
            onClick={() => onChange(tab.id)}
            style={{ color: selected ? 'var(--ink-100)' : 'var(--ink-70)' }}
            className={`group flex min-w-40 items-center gap-3 rounded-xl border px-3! py-3! text-left transition-colors lg:min-w-0 ${
              selected
                ? 'border-sky-400/50 bg-sky-500/10 text-[var(--ink-100)] shadow-sm'
                : 'border-transparent bg-transparent text-[var(--ink-50)] hover:border-[var(--card-border)] hover:bg-[var(--glass)] hover:text-[var(--ink-100)]'
            }`}
          >
            <span className={`grid size-7 shrink-0 place-items-center rounded-lg text-xs font-semibold ${selected ? 'bg-sky-500 text-white' : 'bg-[var(--glass)] text-[var(--ink-50)]'}`}>
              {String(index + 1).padStart(2, '0')}
            </span>
            <span className="min-w-0 truncate text-base font-medium">{tab.name}</span>
          </button>
        )
      })}
    </nav>
  )
}
