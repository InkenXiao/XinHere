// 顶栏 XuanPu 消息铃铛：未读角标 + 下拉最近消息（数字分身/AI 任务通知），点击免登打开关联页面/文档
import { useEffect, useRef, useState } from 'react'
import { useAuthStore } from '@/state/authStore'
import { useXuanpuMsgStore, type XuanpuMsg } from '@/state/xuanpuMsgStore'

function fmtTime(ts: number) {
  if (!ts) return ''
  const d = new Date(ts)
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  const today = new Date()
  return d.toDateString() === today.toDateString()
    ? hm
    : `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${hm}`
}

export default function XuanpuMsgBell() {
  const user = useAuthStore((s) => s.user)
  const items = useXuanpuMsgStore((s) => s.items)
  const unread = useXuanpuMsgStore((s) => s.unread)
  const markRead = useXuanpuMsgStore((s) => s.markRead)
  const openMsg = useXuanpuMsgStore((s) => s.openMsg)
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (user) useXuanpuMsgStore.getState().startPolling()
    else useXuanpuMsgStore.getState().stopPolling()
  }, [user])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  if (!user) return null

  const onPick = (m: XuanpuMsg) => {
    if (m.url) void openMsg(m)
  }

  return (
    <div className="msg-bell" ref={wrapRef}>
      <button
        className="bell-btn"
        aria-label="平台消息"
        onClick={() => {
          setOpen((v) => !v)
          if (!open) markRead()
        }}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M12 3a6 6 0 0 0-6 6v3.2l-1.4 2.8a1 1 0 0 0 .9 1.5h13a1 1 0 0 0 .9-1.5L18 12.2V9a6 6 0 0 0-6-6Zm-2 15a2 2 0 0 0 4 0"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        {unread > 0 && <span className="bell-badge">{unread > 99 ? '99+' : unread}</span>}
      </button>
      {open && (
        <div className="msg-pop">
          <div className="msg-pop-hd">平台消息</div>
          {items.length === 0 ? (
            <div className="msg-empty">暂无消息</div>
          ) : (
            items.slice(0, 12).map((m) => (
              <button
                key={m.id}
                className={`msg-item${m.url ? ' link' : ''}`}
                onClick={() => onPick(m)}
              >
                <span className="msg-title">{m.title || m.body || '通知'}</span>
                {m.body && m.title ? <span className="msg-body">{m.body}</span> : null}
                <span className="msg-time">{fmtTime(m.ts)}</span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  )
}
