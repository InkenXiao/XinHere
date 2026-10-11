// iframe 填报宿主：srcdoc 内嵌后端渲染的 HTML 表单，监听 xinhere-* 协议
// （ready → 高度自适应 / draft → 保存草稿 / submit → 正式提交）。
// sandbox 不含 allow-same-origin：表单提交走 postMessage，无需访问父页 DOM。
import { useState } from 'react'
import { useIframeMessaging } from '../hooks/useIframeMessaging'

const MIN_HEIGHT = 320

export function IframeFillHost({
  html,
  onSubmit,
  onSaveDraft,
  maxHeight = '62vh',
}: {
  html: string
  onSubmit: (data: Record<string, unknown>) => void
  onSaveDraft: (data: Record<string, unknown>) => void
  maxHeight?: string
}) {
  const [height, setHeight] = useState(MIN_HEIGHT)
  const { bindIframe } = useIframeMessaging((msg) => {
    if (msg.type === 'xinhere-ready') setHeight(Math.max(MIN_HEIGHT, Math.min(msg.height, 2000)))
    else if (msg.type === 'xinhere-draft') onSaveDraft(msg.data)
    else if (msg.type === 'xinhere-submit') onSubmit(msg.data)
  })

  return (
    <div className="w-full overflow-y-auto rounded-md border" style={{ borderColor: 'var(--card-border)', maxHeight }}>
      <iframe
        ref={bindIframe}
        title="动态填报表单"
        srcDoc={html}
        sandbox="allow-scripts allow-forms"
        className="w-full block border-0 bg-white"
        style={{ height }}
      />
    </div>
  )
}
