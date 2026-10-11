// 动态填报弹窗：fetch 带鉴权拿填报 HTML（iframe src 无法带 Bearer）→ srcdoc 内嵌
// → 监听 xinhere-submit 提交 → dynamic_fill_data 落库 + 待办两段式流转。
import { useEffect, useState } from 'react'
import { useTodoStore } from '@/scms/todoShim'
import { useUiStore } from '@/scms/uiShim'
import ScmsModal from '@/scms/common/ScmsModal'
import { getFillPage, saveFillDraft, submitFill } from '../api'
import { IframeFillHost } from './IframeFillHost'
import { TabbedFillForm } from './TabbedFillForm'
import type { FillPage } from '../types'
import type { TodoItem } from '@/scms/types'

export function FillModal({ todo, onClose }: { todo: TodoItem; onClose: () => void }) {
  const toast = useUiStore((s) => s.toast)
  const [html, setHtml] = useState('')
  const [name, setName] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [page, setPage] = useState<FillPage | null>(null)
  const templateId = todo.dynamic_template_id ?? ''
  const readOnly = todo.status === 'completed' || todo.status === 'na_closed'

  useEffect(() => {
    if (!templateId) {
      setErr('待办缺少动态模板信息，无法填报')
      return
    }
    // silent：失败内联展示，不弹全局 toast
    getFillPage(templateId, todo.todo_id)
      .then((p) => {
        setPage(p)
        setHtml(p.html)
        setName(p.name)
      })
      .catch((e: Error) => setErr(e.message || '加载填报页失败'))
  }, [templateId, todo.todo_id])

  const onSubmit = async (data: Record<string, unknown>) => {
    if (busy || readOnly) return
    setBusy(true)
    try {
      await submitFill(templateId, todo.todo_id, data)
      toast('已提交，等待派发人审核')
      onClose()
      void useTodoStore.getState().load() // 立即刷新列表（SSE 推送兜底）
    } catch {
      setBusy(false) // 失败提示由拦截器统一弹；弹窗保留可重填
    }
  }

  const onSaveDraft = async (data: Record<string, unknown>) => {
    if (busy || readOnly) return
    setBusy(true)
    try {
      await saveFillDraft(templateId, todo.todo_id, data)
      toast('草稿已保存，下次进入可继续填写')
    } catch {
      // 请求层统一展示错误，保留当前输入供用户重试。
    } finally {
      setBusy(false)
    }
  }

  return (
    <ScmsModal title={name || '信息填报'} onClose={onClose} closable={!busy} closeOnMask={false} centered width="min(1440px, 98vw)" bodyMaxHeight="82vh" light>
      <div className="bg-white px-1! py-1! text-slate-700 sm:px-2!">
        <div className="flex flex-col gap-3 rounded-2xl border border-slate-200 bg-sky-50 px-4! py-4! shadow-sm sm:flex-row sm:items-center sm:justify-between sm:px-5!">
          <div className="min-w-0">
            <div className="mb-1! text-xs font-medium tracking-wide text-sky-600">信息填报任务</div>
            <h2 className="break-words text-lg font-semibold text-slate-900">{todo.title}</h2>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full border border-[var(--card-border)] bg-[var(--pop-bg)] px-3! py-1.5! text-[var(--ink-50)]">发起人 · <b className="text-[var(--ink-70)]">{todo.dispatcher_name}</b></span>
            {todo.status === 'submitted' && <span className="rounded-full bg-amber-500/12 px-3! py-1.5! font-medium text-amber-600">待审核 · 可继续修改</span>}
          </div>
        </div>
        {err ? (
          <div className="tcc-err">{err}</div>
        ) : page?.tabs?.length ? (
          <TabbedFillForm key={todo.todo_id} tabs={page.tabs} columns={page.columns ?? []} initial={page.initial ?? {}} busy={busy} readOnly={readOnly} onSaveDraft={(data) => void onSaveDraft(data)} onSubmit={(data) => void onSubmit(data)} />
        ) : !html ? (
          <div className="tcc-note-txt">正在加载填报表单…</div>
        ) : (
          <div className="mt-4! overflow-hidden rounded-2xl border border-slate-200 bg-white p-2! shadow-sm">
            {readOnly && <div className="mb-2! rounded-xl border border-sky-400/20 bg-sky-500/8 px-4! py-3! text-base text-sky-500">本次填报已完成，当前内容仅供查看</div>}
            <div className={readOnly ? 'pointer-events-none opacity-80' : ''}><IframeFillHost html={html} onSaveDraft={(d) => void onSaveDraft(d)} onSubmit={(d) => void onSubmit(d)} maxHeight="74vh" /></div>
            {busy && <div className="px-2! py-2! text-center text-xs text-[var(--ink-50)]">正在提交，请稍候…</div>}
          </div>
        )}
      </div>
    </ScmsModal>
  )
}
