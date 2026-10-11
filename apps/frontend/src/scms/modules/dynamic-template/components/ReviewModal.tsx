import { useEffect, useState } from 'react'
import ScmsModal from '@/scms/common/ScmsModal'
import { api } from '@/scms/api'
import { useTodoStore } from '@/scms/todoShim'
import { useUiStore } from '@/scms/uiShim'
import type { TaskBoard, TodoDetail, TodoItem } from '@/scms/types'
import { fmtTime } from '@/utils'
import { TODO_STATUS_ZH } from '@/utils'
import { getTaskFillReview, type DynamicTemplateDetail } from '../api'
import { TemplateTabs } from './TemplateTabs'

export function ReviewModal({ todo, onClose }: { todo: TodoItem; onClose: () => void }) {
  const toast = useUiStore((s) => s.toast)
  const [detail, setDetail] = useState<TodoDetail | null>(null)
  const [todos, setTodos] = useState<TodoDetail[]>([])
  const [board, setBoard] = useState<TaskBoard | null>(null)
  const [template, setTemplate] = useState<DynamicTemplateDetail | null>(null)
  const [rejecting, setRejecting] = useState(false)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmingAll, setConfirmingAll] = useState(false)
  const [err, setErr] = useState('')
  const [activeTab, setActiveTab] = useState('')
  const templateId = todo.dynamic_template_id ?? ''

  const load = (preferredTodoId = todo.todo_id) => {
    if (!templateId) {
      setErr('待办缺少动态模板信息，无法查看填报数据')
      return
    }
    setErr('')
    getTaskFillReview(todo.task_id, templateId)
      .then(({ board: nextBoard, todos: nextTodos, template: nextTemplate }) => {
        setBoard(nextBoard)
        setTodos(nextTodos)
        setDetail(nextTodos.find((item) => item.todo_id === preferredTodoId) ?? nextTodos[0] ?? null)
        setTemplate(nextTemplate)
        setActiveTab(nextTemplate.tabs?.[0]?.id ?? '')
      })
      .catch((e: Error) => setErr(e.message || '加载填报数据失败'))
  }

  useEffect(() => {
    load(todo.todo_id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateId, todo.todo_id])

  const review = async (approve: boolean) => {
    if (busy || !detail) return
    if (!approve && !comment.trim()) return
    setBusy(true)
    try {
      await useTodoStore.getState().review(detail.todo_id, approve, comment.trim() || undefined)
      toast(approve ? '审核已通过' : '已退回办理人重新填报')
      const next = todos.find((item) => item.todo_id !== detail.todo_id && item.status === 'submitted')
      await useTodoStore.getState().load()
      setRejecting(false)
      setComment('')
      setBusy(false)
      load(next?.todo_id ?? detail.todo_id)
    } catch {
      setBusy(false)
    }
  }

  const tab = template?.tabs?.find((item) => item.id === activeTab)
  const columns = (template?.columns ?? []).filter((field) => !tab || tab.field_keys.includes(field.key))
  const payload = detail?.payload ?? {}
  const rowCount = tab?.presentation === 'grid' ? Math.max(1, ...columns.map((field) => Array.isArray(payload[field.key]) ? (payload[field.key] as unknown[]).length : 1)) : 0
  const canReview = detail?.status === 'submitted'
  const pendingReviews = todos.filter((item) => item.status === 'submitted')

  const approveAll = async () => {
    if (busy || pendingReviews.length === 0) return
    setConfirmingAll(false)
    setBusy(true)
    let approved = 0
    try {
      for (const item of pendingReviews) {
        await api('POST', `/todos/${item.todo_id}/review`, { approve: true, comment: null })
        approved += 1
      }
      toast(`已通过全部 ${approved} 条填报`)
    } catch {
      if (approved > 0) toast(`已通过 ${approved} 条，其余记录审核失败，请重试`)
    } finally {
      await useTodoStore.getState().load()
      setBusy(false)
      load(detail?.todo_id)
    }
  }

  return (
    <ScmsModal title={`填报审核 · ${template?.name || todo.title}`} onClose={onClose} centered width="min(1440px, 98vw)" bodyMaxHeight="82vh" light>
      <div className="text-slate-700">
        {err ? (
          <div className="tcc-err">{err}</div>
        ) : !detail || !template || !board ? (
          <div className="tcc-note-txt">正在加载填报数据…</div>
        ) : (
          <div className="grid overflow-hidden rounded-2xl border border-slate-200 bg-white lg:grid-cols-[280px_minmax(0,1fr)]">
            <aside className="border-b border-slate-200 bg-slate-50 p-5! lg:border-r lg:border-b-0">
              <div className="mb-4!"><div className="text-xs font-medium tracking-wide text-sky-600">任务审核进度</div><h3 className="mt-1! text-lg font-semibold text-slate-800">{board.title}</h3><p className="mt-2! text-xs leading-5 text-slate-500">共 {todos.length} 人 · 待审核 {todos.filter((item) => item.status === 'submitted').length} 人</p></div>
              <div className="flex gap-2 overflow-x-auto pb-1! lg:max-h-[68vh] lg:flex-col lg:overflow-y-auto lg:pb-0!">
                {todos.map((item, index) => {
                  const selected = item.todo_id === detail.todo_id
                  return <button key={item.todo_id} type="button" onClick={() => { setDetail(item); setRejecting(false); setComment('') }} className={`flex min-w-48 items-center gap-3 rounded-xl border p-3! text-left transition-colors lg:min-w-0 ${selected ? 'border-sky-300 bg-sky-50' : 'border-transparent hover:border-slate-200 hover:bg-white'}`}>
                    <span className={`grid size-8 shrink-0 place-items-center rounded-lg text-xs font-semibold ${selected ? 'bg-sky-500 text-white' : 'bg-slate-200 text-slate-500'}`}>{String(index + 1).padStart(2, '0')}</span>
                    <span className="min-w-0 flex-1"><b className="block truncate text-base">{item.assignee_name || item.assignee_id}</b><span className={`mt-1! block text-xs ${item.status === 'submitted' ? 'text-amber-500' : item.status === 'completed' ? 'text-emerald-500' : 'text-[var(--ink-50)]'}`}>{TODO_STATUS_ZH[item.status] ?? item.status}</span></span>
                  </button>
                })}
              </div>
            </aside>
            <section className="min-w-0 p-5! sm:p-8! lg:p-10!">
              <div className="mb-5! flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 pb-4!">
                <div><div className="text-xs text-slate-500">当前查看</div><h3 className="mt-1! text-lg font-semibold text-slate-900">{detail.assignee_name || detail.assignee_id}</h3></div>
                <div className="text-right text-xs leading-5 text-slate-500"><div className="font-medium text-amber-600">{TODO_STATUS_ZH[detail.status] ?? detail.status}</div><div>{detail.submitted_at ? `提交于 ${fmtTime(detail.submitted_at)}` : '尚未提交'}</div></div>
              </div>
              {!!template.tabs?.length && <div className="mb-5!"><TemplateTabs tabs={template.tabs} active={activeTab} onChange={setActiveTab} layout="tabs" enhancedScroll /></div>}
              <div className="overflow-hidden rounded-xl border border-slate-200 bg-white [&_th]:!border-slate-200 [&_th]:!bg-slate-50 [&_th]:!text-slate-600 [&_td]:!border-slate-200 [&_td]:!text-slate-700">
            {rowCount > 0 ? (
              <div className="overflow-x-auto"><table className="w-full border-collapse text-base"><thead><tr><th className="border-b border-r border-[var(--card-border)] bg-black/[0.025] p-3! text-center text-xs text-[var(--ink-50)]">序号</th>{columns.map((field) => <th key={field.key} className="min-w-40 border-b border-r border-[var(--card-border)] bg-black/[0.025] p-3! text-left font-medium text-[var(--ink-70)] last:border-r-0">{field.label}</th>)}</tr></thead><tbody>{Array.from({ length: rowCount }, (_, row) => <tr key={row} className="border-b border-[var(--card-border)] last:border-b-0"><td className="border-r border-[var(--card-border)] p-3! text-center text-xs text-[var(--ink-50)]">{String(row + 1).padStart(2, '0')}</td>{columns.map((field) => { const values = Array.isArray(payload[field.key]) ? payload[field.key] as unknown[] : [payload[field.key]]; return <td key={field.key} className="border-r border-[var(--card-border)] p-3! text-[var(--ink-90)] last:border-r-0">{formatValue(values[row])}</td> })}</tr>)}</tbody></table></div>
            ) : columns.map((field) => (
              <div
                key={field.key}
                className="grid gap-2 border-b px-4! py-3.5! text-base last:border-b-0 sm:grid-cols-[minmax(140px,0.35fr)_1fr] sm:gap-5"
                style={{ borderColor: 'var(--card-border)' }}
              >
                <span style={{ color: 'var(--ink-60)' }}>{field.label}</span>
                <span className="whitespace-pre-wrap break-words" style={{ color: 'var(--ink-90)' }}>
                  {formatValue(payload[field.key])}
                </span>
              </div>
            ))}
            {columns.length === 0 && <div className="p-3 text-base">该模板没有可展示字段</div>}
              </div>

              {rejecting && canReview && (
                <div className="mt-4!">
                  <textarea autoFocus className="fill-input min-h-24" placeholder="请填写退回原因，办理人修改后可以重新提交" value={comment} onChange={(e) => setComment(e.target.value)} />
                </div>
              )}

              <div className="mt-5! flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4!">
                <button className="btn-primary px-5!" disabled={busy || pendingReviews.length === 0} onClick={() => setConfirmingAll(true)}>
                  {busy ? '处理中…' : `全部通过${pendingReviews.length ? `（${pendingReviews.length}）` : ''}`}
                </button>
                <div className="flex flex-wrap justify-end gap-2">
                <button className="btn-ghost" disabled={busy} onClick={onClose}>关闭</button>
                {canReview && !rejecting && <><button className="btn-ghost" disabled={busy} onClick={() => setRejecting(true)}>退回修改</button><button className="btn-primary" disabled={busy} onClick={() => void review(true)}>{busy ? '处理中…' : '审核通过'}</button></>}
                {canReview && rejecting && <><button className="btn-ghost" disabled={busy} onClick={() => setRejecting(false)}>取消退回</button><button className="btn-primary" disabled={busy || !comment.trim()} onClick={() => void review(false)}>{busy ? '处理中…' : '确认退回'}</button></>}
                </div>
              </div>
            </section>
          </div>
        )}
        {confirmingAll && (
          <div className="fixed inset-0 z-[90] grid place-items-center bg-slate-950/70 p-4! backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="approve-all-title" onClick={(e) => { if (e.target === e.currentTarget) setConfirmingAll(false) }}>
            <div className="relative w-full max-w-md overflow-hidden rounded-3xl border border-sky-400/25 bg-[var(--pop-bg)] p-6! shadow-[0_28px_90px_rgba(0,0,0,.55)] sm:p-7!">
              <div className="pointer-events-none absolute -right-12 -top-14 size-40 rounded-full bg-sky-400/15 blur-3xl" />
              <div className="relative">
                <div className="mb-5! flex items-center gap-4">
                  <span className="grid size-12 shrink-0 place-items-center rounded-2xl border border-sky-400/30 bg-sky-500/15 text-2xl text-sky-400">✓</span>
                  <div><div className="text-xs font-medium tracking-[.18em] text-sky-500">BATCH APPROVAL</div><h3 id="approve-all-title" className="mt-1! text-xl font-semibold text-[var(--ink-100)]">确认全部通过？</h3></div>
                </div>
                <p className="text-base leading-7 text-[var(--ink-70)]">将通过当前任务下全部 <strong className="px-1! text-lg tabular-nums text-sky-500">{pendingReviews.length}</strong> 条待审核填报，办理结果会立即生效。</p>
                <div className="mt-5! rounded-2xl border border-amber-400/20 bg-amber-500/8 px-4! py-3! text-xs leading-5 text-amber-500">请确认已完成必要的数据核验。未提交、已完成及已退回的记录不会受到影响。</div>
                <div className="mt-7! flex justify-end gap-3">
                  <button type="button" className="btn-ghost min-w-24 px-4! py-2.5!" onClick={() => setConfirmingAll(false)}>暂不操作</button>
                  <button type="button" className="btn-primary min-w-36 px-5! py-2.5!" onClick={() => void approveAll()}>确认全部通过</button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </ScmsModal>
  )
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.map(formatValue).join('、')
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}
