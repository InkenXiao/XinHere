// 助手 Markdown 渲染（视觉基线参考 deepseek-harness ui-primitives markdown.css：
// 表格横向滚动容器、标题/列表/引用间距体系、行内代码胶囊），映射到 XinHere 主题变量。
// 解析器 react-markdown + remark-gfm 与 dsh 的 mdast-util-from-markdown 同属 micromark/mdast 内核。
// ```chart 围栏 → MdChart（query_data 问数答案内嵌结构化图表 JSON，echarts 出图）。
//
// components 必须是模块级常量：内联对象每次渲染都是新引用，react-markdown 会把
// 其当作组件类型变化而整树卸载重建——图表（echarts dispose/init）随之抖动。
// Markdown 本体 memo：输入框击键等无关状态更新不再触发历史消息重渲染。
import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { MdChart } from './MdChart'
import { MdMermaid } from './MdMermaid'
import { isMermaidSource, MERMAID_FLOW_GUARD } from './mermaidSource'
import { SourceList, parseSourcesPayload } from './SourceList'
import { useUiStore } from '@/scms/uiShim'

/** 过滤 <think> 思考文本：完整块整体剔除；流式半开块（只有 <think> 无闭合）从标签处截断隐藏；
 * 游离的 </think>（网关剥思考正文后残留的闭合标签）单独剔除 */
function stripThink(text: string): string {
  return text
    .replace(/<think>[\s\S]*?<\/think>/g, '')
    .replace(/<think>[\s\S]*$/, '')
    .replace(/<\/think>/g, '')
}

/** 剥离 ```html 围栏与裸 HTML 文档（模型调 generate_html_report 后复读整份 HTML 的消息体）：
 * 预览/下载统一由 html-report/card 卡片承担，正文不渲染源码；
 * 流式未闭合围栏/文档截到末尾 */
export function stripHtmlFences(text: string): string {
  return text
    .replace(/```html[\s\S]*?(```|$)/g, '')
    .replace(/<!DOCTYPE html>[\s\S]*?(<\/html>|$)/gi, '')
    .replace(/<html[\s\S]*?(<\/html>|$)/gi, '')
}

/** 剥离模型复读的 PptSpec JSON（调 generate_ppt 后把大纲 JSON 吐进正文）：
 * 覆盖 ```json 围栏与裸对象两种形态；仅在整体可解析且含 slides[].layout 时剔除，
 * 不影响正常的 JSON 类回答。PPT 内容只存在于文件里，正文不渲染大纲 */
export function stripPptSpecJson(text: string): string {
  let out = text.replace(/```json\s*\{[\s\S]*?"slides"[\s\S]*?\}\s*```/g, '')
  const s = out.indexOf('{')
  if (s < 0) return out
  const e = out.lastIndexOf('}')
  if (e <= s) return out
  try {
    const obj = JSON.parse(out.slice(s, e + 1))
    if (obj && Array.isArray(obj.slides)
      && obj.slides.some((x: unknown) => !!x && typeof (x as { layout?: unknown }).layout === 'string')) {
      out = out.slice(0, s) + out.slice(e + 1)
    }
  } catch {
    // 非 JSON（正常文本含花括号）不动
  }
  return out
}

/** 统一剥离链：think 块 + HTML 复读（围栏/裸文档）+ PptSpec JSON 复读。
 * Markdown 渲染、ChatPanel 空消息判断、AnswerMarkdown 显隐共用，保证口径一致 */
export function stripAIDumps(text: string): string {
  return stripPptSpecJson(stripHtmlFences(stripThink(text)))
}

const mdComponents = {
  table: ({ children }: { children?: React.ReactNode }) => (
    <div className="md-table-scroll">
      <table>{children}</table>
    </div>
  ),
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => {
    // 角标引用链接（AnswerMarkdown 把正文 [n] 转成 [n](#cite-n)）：点击定位来源卡命中片段
    if (href?.startsWith('#cite-')) {
      const n = Number(href.slice('#cite-'.length))
      if (Number.isInteger(n) && n > 0) {
        return (
          <button
            type="button"
            onClick={() => useUiStore.getState().requestCiteFocus(n)}
            title={`定位来源 [${n}]`}
            className="inline-flex h-4! min-w-4! items-center justify-center rounded-full! border-none! bg-[var(--accent)]/15! p-0! px-1! align-super text-xs! font-semibold text-[var(--accent)]! transition-colors hover:bg-[var(--accent)]/30!"
          >
            {children}
          </button>
        )
      }
    }
    return (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    )
  },
  code: ({ className, children, node, ...rest }: React.ComponentPropsWithoutRef<'code'> & { node?: unknown }) => {
    const lang = /language-([\w-]+)/.exec(className ?? '')?.[1]
    const text = String(children)
    // ```mermaid 围栏（或无标注但首行是流程图语法的保守嗅探）→ mermaid 出图
    if (
      lang?.toLowerCase().startsWith('mermaid') ||
      (!lang && isMermaidSource(text) && MERMAID_FLOW_GUARD.test(text))
    ) {
      return <MdMermaid code={text} />
    }
    if (lang === 'chart') {
      return <MdChart code={text} />
    }
    // ```html 围栏：模型复读报告 HTML 时不在正文展示——预览/下载统一由 html-report/card 卡片承担
    if (lang === 'html') {
      return null
    }
    if (lang === 'sources') {
      // 结构化溯源围栏兜底分发（AnswerMarkdown 已自行剥离控制显隐，此分支覆盖非答案路径）
      const payload = parseSourcesPayload(String(children))
      if (payload) return <SourceList data={payload} />
    }
    return (
      <code className={className} {...rest}>
        {children}
      </code>
    )
  },
}

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
        {stripAIDumps(text)}
      </ReactMarkdown>
    </div>
  )
})
