// iframe postMessage 通信 Hook：监听 fill_form.j2 内嵌 JS 上抛的 xinhere-* 协议消息。
// 安全校验：e.source 必须是我们持有的 iframe.contentWindow（丢弃任意第三方窗口伪造消息）。
import { useCallback, useEffect, useRef } from 'react'
import type { XhIframeMessage } from '../types'

export function useIframeMessaging(onMessage: (msg: XhIframeMessage) => void) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  // 回调装进 ref：onMessage 变化（父组件重渲染）不重建监听器
  const cbRef = useRef(onMessage)
  cbRef.current = onMessage

  useEffect(() => {
    const handler = (e: MessageEvent) => {
      const win = iframeRef.current?.contentWindow
      if (!win || e.source !== win) return
      const d = e.data as Partial<XhIframeMessage> | null
      if (!d || typeof d.type !== 'string' || !d.type.startsWith('xinhere-')) return
      if (d.type === 'xinhere-ready' && typeof d.height !== 'number') return
      if (d.type === 'xinhere-draft' && typeof d.data !== 'object') return
      if (d.type === 'xinhere-submit' && typeof d.data !== 'object') return
      cbRef.current(d as XhIframeMessage)
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [])

  /** iframe ref 回调（挂到 <iframe ref={bindIframe}>） */
  const bindIframe = useCallback((el: HTMLIFrameElement | null) => {
    iframeRef.current = el
  }, [])

  return { bindIframe }
}
