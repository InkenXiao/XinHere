// 轻量 Markdown 渲染：表格(GFM)/代码块/标题/列表/引用，供 assistant 消息与流式文本使用
// react-markdown 不渲染原始 HTML，agent 输出无需额外消毒
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function Markdown({ text }: { text: string }) {
  return (
    <div className="md-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: (p) => <a {...p} target="_blank" rel="noreferrer" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
