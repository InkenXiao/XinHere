// 消息面板：未读/已读分组 + 单条已读/全部已读
import { useMemo } from 'react'
import { useNotificationStore } from '@/state/notificationStore'
import type { NotificationItem } from '@/state/notificationStore'
import { fmtTime } from '@/utils'

const CATEGORY_ZH: Record<NotificationItem['category'], string> = {
  todo_remind: '催办',
  todo_submitted: '提交',
  todo_completed: '完成',
  todo_returned: '退回',
}

export default function NotificationPanel() {
  const items = useNotificationStore((s) => s.items)
  const markRead = useNotificationStore((s) => s.markRead)
  const markAllRead = useNotificationStore((s) => s.markAllRead)

  const { unread, read } = useMemo(
    () => ({ unread: items.filter((n) => !n.is_read), read: items.filter((n) => n.is_read) }),
    [items],
  )

  const row = (n: NotificationItem) => (
    <div key={n.notification_id} className={`td-item ${n.is_read ? 'muted' : ''}`}>
      <div className="td-line">
        <span className="td-kind">{CATEGORY_ZH[n.category]}</span>
        <span className="td-title">{n.title}</span>
        <span className="td-time">{fmtTime(n.created_at)}</span>
      </div>
      {n.body && <div className="td-sub">{n.body}</div>}
      <div className="td-line td-meta">
        <span>来自 {n.actor_name || '系统'}</span>
        {!n.is_read && (
          <button className="ghost" onClick={() => void markRead(n.notification_id)}>
            标为已读
          </button>
        )}
      </div>
    </div>
  )

  return (
    <div className="td-panel">
      <div className="td-head">
        <span>未读 {unread.length}</span>
        {unread.length > 0 && (
          <button className="ghost" onClick={() => void markAllRead()}>
            全部已读
          </button>
        )}
      </div>
      {items.length === 0 && <div className="td-empty">暂无消息</div>}
      {unread.length > 0 && <div className="td-group">{unread.map(row)}</div>}
      {read.length > 0 && (
        <details className="td-group-read">
          <summary>已读（{read.length}）</summary>
          <div className="td-group muted">{read.slice(0, 20).map(row)}</div>
        </details>
      )}
    </div>
  )
}
