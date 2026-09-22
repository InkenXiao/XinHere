// 待办面板：单一「我的待办」列表（来源以徽标标识）+ 三动作（反馈/不涉及/忽略）+ 去填报弹窗入口 + na 确认/驳回
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

const FILL_STATUS_ZH: Record<string, string> = { pending: '待填报', submitted: '已提交' }

export default function TodoPanel() {
  const items = useTodoStore((s) => s.items)
  const ignored = useTodoStore((s) => s.ignored)
  const openScene = useUiStore((s) => s.openScene)
  const openFill = useUiStore((s) => s.openFill)
  const xpFills = useTodoStore((s) => s.xpFills)
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
    <div className="todo-list">
      {list.length === 0 && xpFills.length === 0 && <div className="todo-empty">暂无待办</div>}
      {/* 平台填报待办（来源：XuanPu） */}
      {xpFills.map((f) => (
        <div
          className={`td-item ${f.status !== 'pending' ? 'dim' : ''}`}
          key={`xf${f.id}`}
          onClick={() => openFill({ assignmentId: f.id, templateId: f.template_id })}
          style={{ cursor: 'pointer' }}
        >
          <div className="td-top">
            <span className="td-dot" style={{ background: f.status === 'pending' ? '#f59e0b' : '#10b981' }} />
            <span className="td-title">{f.title}</span>
            <span className="td-kind xp">XuanPu</span>
          </div>
          <div className="td-sub">
            {FILL_STATUS_ZH[f.status] ?? f.status}
            {f.submitted_at ? ` · 提交于 ${f.submitted_at}` : ''}
          </div>
          {f.status === 'pending' && (
            <div className="td-actions">
              <button className="primary" onClick={() => openFill({ assignmentId: f.id, templateId: f.template_id })}>
                去填报
              </button>
            </div>
          )}
        </div>
      ))}
      {/* 本站待办（来源：XinHere） */}
      {list.map((t) => (
        <div className={`td-item ${t.status !== 'pending' ? 'dim' : ''}`} key={t.todo_id}>
          <div className="td-top">
            <span className="td-dot" style={{ background: t.lamp ? LAMP_COLOR[t.lamp] : 'var(--ink-30)' }} />
            <span className="td-title">{t.title}</span>
            <span className="td-kind">XinHere</span>
          </div>
          <div className="td-sub">
            {KIND_ZH[t.kind]} · {t.dispatcher_name} · {SCENE_ZH[t.scene] ?? t.scene}
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
  )
}
