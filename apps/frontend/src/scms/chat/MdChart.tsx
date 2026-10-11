// md 内嵌图表（```chart 围栏）：query_data 工具把问数流的 chart 事件 JSON 塞进答案文本，
// 这里用 echarts 渲染（数据结构对齐 workbench ChatChart：labels/series/xAxisName/yAxisName）。
// echarts 按需懒加载（独立 chunk）；主题色 CSS 变量注入；解析/渲染失败降级为源码块。
import { memo, useEffect, useRef, useState } from 'react'

export interface MdChartData {
  title?: string
  labels: string[]
  series: { name: string; data: number[]; type?: string }[]
  xAxisName?: string
  yAxisName?: string
}

let echartsLoader: Promise<typeof import('echarts/core')> | null = null
function loadEcharts() {
  echartsLoader ??= (async () => {
    const [{ use }, charts, components, renderers] = await Promise.all([
      import('echarts/core'),
      import('echarts/charts'),
      import('echarts/components'),
      import('echarts/renderers'),
    ])
    use([charts.BarChart, charts.LineChart,
         components.GridComponent, components.TooltipComponent, components.LegendComponent,
         renderers.CanvasRenderer])
    return (await import('echarts/core'))
  })()
  return echartsLoader
}

/** 组装 option（对齐 workbench ChatChart.buildOption）+ 主题色注入 */
function buildOption(c: MdChartData) {
  const cs = getComputedStyle(document.documentElement)
  const ink = cs.getPropertyValue('--ink-70').trim() || '#888'
  const faint = cs.getPropertyValue('--glass-bd').trim() || '#ccc'
  return {
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, textStyle: { color: ink } },
    grid: { top: 10, right: 16, bottom: 40, left: 48, containLabel: false },
    xAxis: {
      type: 'category', data: c.labels, name: c.xAxisName || '',
      axisLabel: { color: ink }, axisLine: { lineStyle: { color: faint } },
    },
    yAxis: {
      type: 'value', name: c.yAxisName || '',
      axisLabel: { color: ink }, axisLine: { lineStyle: { color: faint } },
    },
    textStyle: { color: ink },
    series: c.series.map((s) => ({
      name: s.name, type: s.type === 'bar' ? 'bar' : 'line', data: s.data, smooth: true,
    })),
  }
}

export const MdChart = memo(function MdChart({ code }: { code: string }) {
  const elRef = useRef<HTMLDivElement | null>(null)
  const [failed, setFailed] = useState(false)
  // 解析失败降级：源码块展示，不破坏整条消息渲染
  let data: MdChartData | null = null
  try {
    const parsed = JSON.parse(code)
    if (Array.isArray(parsed?.labels) && Array.isArray(parsed?.series)) data = parsed
  } catch { /* 非 JSON */ }

  useEffect(() => {
    if (!data || !elRef.current) return
    let chart: { setOption: (o: unknown) => void; resize: () => void; dispose: () => void } | null = null
    let ro: ResizeObserver | null = null
    let alive = true
    void loadEcharts().then((mod) => {
      if (!alive || !elRef.current) return
      try {
        chart = mod.init(elRef.current)
        chart.setOption(buildOption(data as MdChartData))
      } catch {
        setFailed(true)
        return
      }
      ro = new ResizeObserver(() => chart?.resize())
      ro.observe(elRef.current)
    })
    return () => {
      alive = false
      ro?.disconnect()
      chart?.dispose()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code])

  if (!data) {
    return (
      <pre className="md-code">
        <code>{code}</code>
      </pre>
    )
  }
  if (failed) {
    return (
      <div className="flex h-[280px] w-full items-center justify-center rounded-xl border border-[var(--glass-bd)] bg-[var(--card-bg)] text-xs text-[var(--muted)]">
        图表渲染失败{data.title ? `（${data.title}）` : ''}
      </div>
    )
  }
  return (
    <div className="my-2 w-full rounded-xl border border-[var(--glass-bd)] bg-[var(--card-bg)] p-3">
      {data.title && (
        <div className="mb-2 text-center text-sm font-semibold text-[var(--ink-100)]">{data.title}</div>
      )}
      <div ref={elRef} className="h-[280px] w-full" />
    </div>
  )
})
