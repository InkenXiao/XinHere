// ```mermaid 围栏 → mermaid 出图。渲染走 mermaid.render（React 侧 useRef 注入 SVG）；
// 源码先经 sanitizeMermaid 容错清洗（移植 xin-knowledge mermaidFencePlugin，见 mermaidSource.ts）；
// mermaid 体积大，与 echarts 同款按需懒加载（独立 chunk）；渲染失败降级为源码块 + 错误提示，不破坏整条消息。
// 主题自适应：按 --ink-100 亮度判断界面明暗，深色界面用 mermaid dark 主题、浅色用 neutral。
import { memo, useEffect, useRef, useState } from 'react'
import { sanitizeMermaid } from './mermaidSource'

type MermaidApi = {
  initialize: (config: Record<string, unknown>) => void
  render: (id: string, source: string) => Promise<{ svg: string }>
}

let mermaidLoader: Promise<MermaidApi> | null = null
function loadMermaid() {
  mermaidLoader ??= import('mermaid').then((m) => m.default as unknown as MermaidApi)
  return mermaidLoader
}

let renderSeq = 0

/** --ink-100 为浅色 ⇒ 深色界面（mermaid dark 主题），否则浅色界面（neutral 主题） */
function pickTheme(): 'dark' | 'neutral' {
  const ink = (getComputedStyle(document.documentElement).getPropertyValue('--ink-100') || '').trim()
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(ink)
  if (!m) return 'neutral'
  let hex = m[1]
  if (hex.length === 3) hex = hex.split('').map((c) => c + c).join('')
  const num = parseInt(hex, 16)
  const lum =
    (0.299 * ((num >> 16) & 255) + 0.587 * ((num >> 8) & 255) + 0.114 * (num & 255)) / 255
  return lum > 0.5 ? 'dark' : 'neutral'
}

export const MdMermaid = memo(function MdMermaid({ code }: { code: string }) {
  const elRef = useRef<HTMLDivElement | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setFailed(null)
    void loadMermaid().then(async (mermaid) => {
      if (!alive || !elRef.current) return
      try {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'loose',
          suppressErrorRendering: true, // 解析失败不往 body 里插错误炸弹，统一走下方降级
          fontFamily: '"PingFang SC","Microsoft YaHei","Noto Sans SC",sans-serif',
          theme: pickTheme(),
          flowchart: {
            htmlLabels: true,
            useMaxWidth: true,
            wrappingWidth: 220,
            nodeSpacing: 36,
            rankSpacing: 48,
            curve: 'basis',
          },
        })
        const { svg } = await mermaid.render(`md-mermaid-${++renderSeq}`, sanitizeMermaid(code))
        if (!alive || !elRef.current) return
        elRef.current.innerHTML = svg
        const svgEl = elRef.current.querySelector('svg')
        if (svgEl) {
          // 只去掉定高；max-width 必须保留 mermaid 写入的「自然尺寸上限」（useMaxWidth），
          // 覆写成 100% 会把小图拉到容器宽等比放大成巨图
          svgEl.removeAttribute('height')
          svgEl.style.height = 'auto'
        }
      } catch (e) {
        if (alive) setFailed(e instanceof Error ? e.message : 'Invalid syntax')
      }
    })
    return () => {
      alive = false
    }
  }, [code])

  if (failed) {
    return (
      <div className="my-2 w-full overflow-hidden rounded-xl border border-[var(--glass-bd)] bg-[var(--card-bg)] p-3">
        <pre className="m-0 whitespace-pre-wrap p-0 text-xs leading-relaxed text-[var(--ink-70)]">
          <code>{code}</code>
        </pre>
        <div className="mt-2 text-xs text-[#f1998f]">流程图语法无法渲染：{failed}</div>
      </div>
    )
  }
  // 容器：块级占位 + 横向滚动（超宽图不撑破消息列）；svg 自适应宽度（参考 .mermaid-block）
  return (
    <div
      ref={elRef}
      className="mermaid-block my-2 w-full max-w-full overflow-x-auto overflow-y-visible px-1 py-2 [&_svg]:h-auto [&_svg]:max-w-full"
    />
  )
})
