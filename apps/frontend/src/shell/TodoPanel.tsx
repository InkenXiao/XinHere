// 待办面板：双盒切换 + 三动作（反馈/不涉及/忽略）+ 去填报弹窗入口 + na 确认/驳回 + XuanPu 待办互通
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/transport/api'
import { useTodoStore } from '@/state/todoStore'
import { useUiStore } from '@/state/uiStore'
import type { TodoItem, XuanPuTodos } from '@/types'
import { SCENE_ZH, TODO_STATUS_ZH, fmtTime } from '@/utils'

const KIND_ZH: Record<TodoItem['kind'], string> = {
  action: '任务',
  review: '审批',
  na_confirm: '不涉及确认',
  feedback_review: '反馈复核',
}

const LAMP_COLOR: Record<string, string> = { r: '#ef4444', y: '#f59e0b', g: '#10b981' }

const XP_PRIO_ZH: Record<string, string> = { low: '低', medium: '中', high: '高', urgent: '紧急' }

const FILL_STATUS_ZH: Record<string, string> = { pending: '待填报', submitted: '已提交' }

export default function TodoPanel() {
  const box = useTodoStore((s) => s.box)
  const items = useTodoStore((s) => s.items)
  const ignored = useTodoStore((s) => s.ignored)
  const setBox = useTodoStore((s) => s.setBox)
  const openScene = useUiStore((s) => s.openScene)
  const openFill = useUiStore((s) => s.openFill)
  const xpFills = useTodoStore((s) => s.xpFills)
  // 内联回复框：记录展开项与模式
  const [reply, setReply] = useState<{ id: string; mode: 'feedback' | 'na' } | null>(null)
  const [replyText, setReplyText] = useState('')
  // XuanPu 待办互通（经 MCP 网关 REST 代理）
  const [xp, setXp] = useState(false)
  const [xpData, setXpData] = useState<XuanPuTodos | null>(null)
  const [xpLoading, setXpLoading] = useState(false)
  const [xpErr, setXpErr] = useState('')
  const [xpName, setXpName] = useState('')

  const loadXp = useCallback(() => {
    setXpLoading(true)
    setXpErr('')
    api<XuanPuTodos>('GET', '/xuanpu/todos?owner=')
      .then((r) => {
        if (r.raw) setXpErr(r.raw)
        else setXpData(r)
      })
      .catch((e: unknown) => setXpErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setXpLoading(false))
  }, [])
  useEffect(() => {
    if (xp) loadXp()
  }, [xp, loadXp])

  const createXp = async () => {
    const name = xpName.trim()
    if (!name) return
    try {
      const r = await api<{ ok?: boolean; raw?: string }>('POST', '/xuanpu/todos', { name })
      if (r.raw) setXpErr(r.raw)
      else {
        setXpName('')
        loadXp()
      }
    } catch (e: unknown) {
      setXpErr(e instanceof Error ? e.message : String(e))
    }
  }

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
      <div className="todo-tabs">
        <div
          className={`todo-tab ${!xp && box === 'assignee' ? 'on' : ''}`}
          onClick={() => {
            setXp(false)
            setBox('assignee')
          }}
        >
          我的待办
        </div>
        <div
          className={`todo-tab ${!xp && box === 'dispatcher' ? 'on' : ''}`}
          onClick={() => {
            setXp(false)
            setBox('dispatcher')
          }}
        >
          我派发的
        </div>
        <div className={`todo-tab ${xp ? 'on' : ''}`} onClick={() => setXp(true)}>
          分身待办
        </div>
      </div>
      {xp ? (
        <div className="todo-list">
          <div className="td-item">
            <div className="td-top">
              <span className="td-title">新建 XuanPu 周任务（默认本周）</span>
            </div>
            <div className="td-actions">
              <input
                value={xpName}
                placeholder="任务名称…"
                onChange={(e) => setXpName(e.target.value)}
                style={{
                  flex: 1,
                  minWidth: 0,
                  background: 'transparent',
                  border: '1px solid var(--rim-line)',
                  color: 'var(--ink)',
                  borderRadius: 6,
                  padding: '4px 8px',
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void createXp()
                }}
              />
              <button className="primary" disabled={!xpName.trim()} onClick={() => void createXp()}>
                创建
              </button>
            </div>
            {xpErr && (
              <div className="td-sub" style={{ color: '#ef4444' }}>
                {xpErr}
              </div>
            )}
          </div>
          {xpLoading && <div className="todo-empty">加载中…</div>}
          {!xpLoading && xpData && xpData.work_tasks.length === 0 && xpData.fill_assignments.length === 0 && (
            <div className="todo-empty">XuanPu 暂无待办</div>
          )}
          {xpData?.work_tasks.map((w) => (
            <div className="td-item" key={`w${w.id}`}>
              <div className="td-top">
                <span className="td-title">{w.name}</span>
                <span className="td-kind">{XP_PRIO_ZH[w.priority] ?? w.priority}</span>
              </div>
              <div className="td-sub">
                {w.owner || '—'} · {w.week_start} ~ {w.week_end}
                {w.is_temporary ? ' · 临时' : ''}
              </div>
              <div className="td-state">{w.status}</div>
            </div>
          ))}
          {xpData && xpData.fill_assignments.length > 0 && (
            <div className="td-item">
              <div className="td-top">
                <span className="td-title">填报任务</span>
              </div>
              {xpData.fill_assignments.map((f) => (
                <div className="td-sub" key={`f${f.id}`} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    {f.title} · {FILL_STATUS_ZH[f.status] ?? f.status}
                    {f.submitted_at ? ` · 提交于 ${f.submitted_at}` : ''}
                  </span>
                  {f.status === 'pending' && (
                    <button className="primary" onClick={() => openFill({ assignmentId: f.id, templateId: f.template_id })}>
                      去填报
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="todo-list">
        {xpFills.length > 0 && (
          <>
            <div className="td-sec">XuanPu 填报</div>
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
                  <span className="td-kind">XuanPu</span>
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
          </>
        )}
        {xpFills.length > 0 && list.length > 0 && <div className="td-sec">XinHere 待办</div>}
        {list.length === 0 && xpFills.length === 0 && <div className="todo-empty">暂无待办</div>}
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
      )}
    </>
  )
}
