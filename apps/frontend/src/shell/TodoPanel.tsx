// 待办面板：双盒切换 + 三动作（反馈/不涉及/忽略）+ 去填报弹窗入口 + na 确认/驳回
import { useState } from 'react'
import { useTodoStore } from '@/state/todoStore'
import { useUiStore } from '@/state/uiStore'
import type { TodoItem } from '@/types'
import { SCENE_ZH, TODO_STATUS_ZH, fmtTime } from '@/utils'

const KIND_ZH: Record<TodoItem['kind'], string> = {
  action: '任务',
  review: '审批',
  na_confirm: '不涉及确认',
  feedback_review: '反馈复核',
}

const LAMP_COLOR: Record<string, string> = { r: '#ef4444', y: '#f59e0b', g: '#10b981' }

export default function TodoPanel({ onCollapse }: { onCollapse?: () => void }) {
  const box = useTodoStore((s) => s.box)
  const items = useTodoStore((s) => s.items)
  const ignored = useTodoStore((s) => s.ignored)
  const setBox = useTodoStore((s) => s.setBox)
  const openScene = useUiStore((s) => s.openScene)
  // 内联回复框：记录展开项与模式
  const [reply, setReply] = useState<{ id: string; mode: 'feedback' | 'na' } | null>(null)
  const [replyText, setReplyText] = useState('')

  const list = items.filter((t) => !ignored.has(t.todo_id))

  const submitReply = async () => {
    if (!reply || !replyText.trim()) return
    const t = replyText.trim()
    setReply(null)
    setReplyText('')
    if (reply.mode === 'feedback') await useTodoStore.getState().feedback(reply.id, t)
    else await useTodoStore.getState().na(reply.id, t)
  }

  const renderActions = (t: TodoItem) => {
    if (t.status !== 'pending') return null
    const st = useTodoStore.getState()
    if (t.kind === 'action') {
      return (
        <div className="td-actions">
          {t.scene !== 'generic' && (
            <button
              className="primary"
              onClick={() => openScene({ scene: t.scene, ref: { ...t.ref, todo_id: t.todo_id }, title: t.title, mode: 'fill' })}
            >
              去填报
            </button>
          )}
          <button onClick={() => setReply({ id: t.todo_id, mode: 'feedback' })}>反馈</button>
          <button className="fb" onClick={() => setReply({ id: t.todo_id, mode: 'na' })}>
            不涉及
          </button>
          <button onClick={() => st.ignore(t.todo_id)}>忽略</button>
        </div>
      )
    }
    if (t.kind === 'review') {
      return (
        <div className="td-actions">
          <button
            className="primary"
            onClick={() => openScene({ scene: t.scene, ref: { ...t.ref, todo_id: t.todo_id }, title: t.title, mode: 'review' })}
          >
            去审批
          </button>
          <button onClick={() => void st.complete(t.todo_id)}>完成</button>
        </div>
      )
    }
    if (t.kind === 'na_confirm') {
      return (
        <div className="td-actions">
          <button className="primary" onClick={() => void st.naConfirm(t.todo_id)}>
            确认
          </button>
          <button className="fb" onClick={() => void st.naReject(t.todo_id)}>
            驳回
          </button>
        </div>
      )
    }
    // feedback_review
    return (
      <div className="td-actions">
        <button className="primary" onClick={() => void st.complete(t.todo_id)}>
          查看 / 完成
        </button>
      </div>
    )
  }

  return (
    <>
      <div className="col-head">
        <div className="t">
          <span className="h-ico">✅</span>待办
        </div>
        <button className="icon-btn" onClick={onCollapse} title="收起">
          ›
        </button>
      </div>
      <div className="todo-tabs">
        <div className={`todo-tab ${box === 'assignee' ? 'on' : ''}`} onClick={() => setBox('assignee')}>
          我的待办
        </div>
        <div className={`todo-tab ${box === 'dispatcher' ? 'on' : ''}`} onClick={() => setBox('dispatcher')}>
          我派发的
        </div>
      </div>
      <div className="todo-list">
        {list.length === 0 && <div className="todo-empty">暂无待办</div>}
        {list.map((t) => (
          <div className={`td-item ${t.status !== 'pending' ? 'dim' : ''}`} key={t.todo_id}>
            <div className="td-top">
              <span className="td-dot" style={{ background: t.lamp ? LAMP_COLOR[t.lamp] : 'var(--ink-30)' }} />
              <span className="td-title">{t.title}</span>
              <span className={`td-kind ${t.kind === 'na_confirm' ? 'na' : ''}`}>{KIND_ZH[t.kind]}</span>
            </div>
            <div className="td-sub">
              {t.dispatcher_name} · {SCENE_ZH[t.scene] ?? t.scene}
              {t.due ? ` · 截止 ${fmtTime(t.due)}` : ''}
            </div>
            <div className="td-state">{TODO_STATUS_ZH[t.status] ?? t.status}</div>
            {renderActions(t)}
            {reply?.id === t.todo_id && (
              <div className="td-reply">
                <textarea
                  autoFocus
                  placeholder={reply.mode === 'feedback' ? '填写反馈内容…' : '填写不涉及原因…'}
                  value={replyText}
                  onChange={(e) => setReplyText(e.target.value)}
                />
                <div className="rp-btns">
                  <button className="btn-ghost" onClick={() => setReply(null)}>
                    取消
                  </button>
                  <button className="btn-primary" disabled={!replyText.trim()} onClick={() => void submitReply()}>
                    提交
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  )
}
