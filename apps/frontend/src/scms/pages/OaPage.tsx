// OA 中心（oa-workflow 插件独立管理页，可插拔）：待办 / 审批 / 申请 / 派发 面板。
// Tab 与按钮按若依 perms（GET /oa/me）显隐——按钮级权限演示。
// 数据走统一 api() 封装；页面级状态，不新增全局 store。移除插件时整目录删除。
import { useCallback, useEffect, useMemo, useState } from 'react'
import { goBack } from '../hashNav'
import { api } from '../api'
import { useUiStore } from '../uiShim'
import { TODO_STATUS_ZH } from '@/utils'
import type {
  FormSchema,
  OaApprovalItem,
  OaDirectoryUser,
  OaMe,
  OaTemplate,
  OaTodoItem,
} from '../types'

type Tab = 'todos' | 'approvals' | 'requests' | 'new-request' | 'new-task'

const cardCls = 'rounded-xl border p-4'
const cardBorder = { borderColor: 'var(--card-border)' }
const btnPrimary =
  'rounded-md px-3 py-1.5 text-base text-white disabled:opacity-40 disabled:cursor-not-allowed'
const btnGhost =
  'rounded-md border px-3 py-1.5 text-base disabled:opacity-40 disabled:cursor-not-allowed'
const inputCls =
  'w-full rounded-md border px-2.5 py-1.5 text-base bg-transparent outline-none focus:ring-1'

function StatusTag({ status }: { status: string }) {
  const tone =
    status === 'completed'
      ? 'bg-emerald-500/12 text-emerald-600'
      : status === 'submitted'
        ? 'bg-sky-500/12 text-sky-600'
        : status === 'returned'
          ? 'bg-amber-500/15 text-amber-600'
          : 'bg-black/5'
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs ${tone}`}>
      {TODO_STATUS_ZH[status] ?? status}
    </span>
  )
}

function Empty({ text }: { text: string }) {
  return <div className="py-10 text-center text-base opacity-50">{text}</div>
}

/** 审批模板动态表单（发起申请用；复用 FormSchema 契约） */
function TemplateForm({ tpl, values, set }: {
  tpl: OaTemplate
  values: Record<string, unknown>
  set: (k: string, v: unknown) => void
}) {
  return (
    <>
      {tpl.form.fields.map((f) => (
        <label key={f.k} className="block">
          <span className="mb-1 block text-xs opacity-60">
            {f.label}
            {f.required && <span className="text-red-500"> *</span>}
          </span>
          {f.type === 'textarea' ? (
            <textarea
              className={inputCls} style={cardBorder} rows={3}
              value={String(values[f.k] ?? '')} placeholder={f.placeholder || ''}
              onChange={(e) => set(f.k, e.target.value)}
            />
          ) : f.type === 'select' ? (
            <select
              className={inputCls} style={cardBorder} value={String(values[f.k] ?? '')}
              onChange={(e) => set(f.k, e.target.value)}
            >
              <option value="">请选择</option>
              {(f.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : (
            <input
              className={inputCls} style={cardBorder}
              type={f.type === 'number' ? 'number' : f.type === 'date' ? 'date' : 'text'}
              value={String(values[f.k] ?? '')} placeholder={f.placeholder || ''}
              onChange={(e) => set(f.k, f.type === 'number' ? e.target.value : e.target.value)}
            />
          )}
        </label>
      ))}
    </>
  )
}

export default function OaPage() {
    const toast = useUiStore((s) => s.toast)
  const [me, setMe] = useState<OaMe | null>(null)
  const [tab, setTab] = useState<Tab>('todos')
  const [todos, setTodos] = useState<OaTodoItem[]>([])
  const [approvals, setApprovals] = useState<OaApprovalItem[]>([])
  const [requests, setRequests] = useState<OaApprovalItem[]>([])
  const [templates, setTemplates] = useState<OaTemplate[]>([])
  const [directory, setDirectory] = useState<OaDirectoryUser[]>([])
  const [busy, setBusy] = useState(false)
  // 逐条审批/提交的展开行与意见
  const [openId, setOpenId] = useState('')
  const [opinion, setOpinion] = useState('')

  // 发起申请表单
  const [tplKey, setTplKey] = useState('')
  const [formVals, setFormVals] = useState<Record<string, unknown>>({})
  // 派发表单
  const [taskTitle, setTaskTitle] = useState('')
  const [taskNote, setTaskNote] = useState('')
  const [taskDue, setTaskDue] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())

  const can = useCallback(
    (perm: string) => !!me && (me.is_admin || me.perms.includes(perm)),
    [me],
  )
  const tabs = useMemo(
    () =>
      [
        { key: 'todos' as Tab, label: `我的待办${todos.length ? ` (${todos.length})` : ''}`, show: can('oa:task:list') },
        { key: 'approvals' as Tab, label: `待我审批${approvals.length ? ` (${approvals.length})` : ''}`, show: can('oa:approval:list') },
        { key: 'requests' as Tab, label: '我发起的', show: can('oa:request:list') },
        { key: 'new-request' as Tab, label: '＋ 发起申请', show: can('oa:request:submit') },
        { key: 'new-task' as Tab, label: '＋ 派发任务', show: can('oa:task:dispatch') },
      ].filter((t) => t.show),
    [can, todos.length, approvals.length],
  )
  const tpl = templates.find((t) => t.template_key === tplKey)
  const reqMissing = useMemo(
    () => (tpl ? tpl.form.fields.filter((f) => {
      const v = formVals[f.k]
      return f.required && (v === undefined || v === null || v === '')
    }) : []),
    [tpl, formVals],
  )

  const loadLists = useCallback(() => {
    if (can('oa:task:list'))
      api<OaTodoItem[]>('GET', '/oa/my/todos', undefined, { silent: true }).then((r) => setTodos(r.data ?? [])).catch(() => {})
    if (can('oa:approval:list'))
      api<OaApprovalItem[]>('GET', '/oa/my/approvals', undefined, { silent: true }).then((r) => setApprovals(r.data ?? [])).catch(() => {})
    if (can('oa:request:list'))
      api<OaApprovalItem[]>('GET', '/oa/my/requests', undefined, { silent: true }).then((r) => setRequests(r.data ?? [])).catch(() => {})
  }, [can])

  useEffect(() => {
    api<OaMe>('GET', '/oa/me', undefined, { silent: true })
      .then((r) => setMe(r.data))
      .catch(() => toast('OA 身份加载失败', 'err'))
  }, [toast])

  useEffect(() => {
    if (me) loadLists()
  }, [me, loadLists])

  useEffect(() => {
    if (tab === 'new-request' && !templates.length)
      api<OaTemplate[]>('GET', '/oa/templates', undefined, { silent: true })
        .then((r) => {
          setTemplates(r.data ?? [])
          if (r.data?.[0]) setTplKey(r.data[0].template_key)
        })
        .catch(() => {})
    if (tab === 'new-task' && !directory.length)
      api<OaDirectoryUser[]>('GET', '/oa/directory', undefined, { silent: true })
        .then((r) => setDirectory(r.data ?? []))
        .catch(() => {})
  }, [tab, templates.length, directory.length])

  useEffect(() => setFormVals({}), [tplKey])

  const act = async (fn: () => Promise<unknown>, okMsg: string) => {
    setBusy(true)
    try {
      await fn()
      toast(okMsg)
      setOpenId('')
      setOpinion('')
      loadLists()
    } catch {
      /* api() 已 toast */
    } finally {
      setBusy(false)
    }
  }

  const payloadRows = (it: { payload?: Record<string, unknown> | null; form?: FormSchema | null }) =>
    Object.entries(it.payload ?? {}).map(([k, v]) => [
      it.form?.fields.find((f) => f.k === k)?.label || k,
      String(v ?? ''),
    ]) as [string, string][]

  if (!me)
    return (
      <div className="app-bg" >
        <div className="flex h-screen items-center justify-center text-base opacity-60">OA 身份加载中…</div>
      </div>
    )

  return (
    <div className="relative min-h-screen">
      <div className="app-bg" />
      <div className="relative z-10 mx-auto max-w-4xl px-4 py-6">
        {/* 顶栏 */}
        <div className="mb-4 flex items-center gap-3">
          <button className={btnGhost} style={cardBorder} onClick={() => goBack()}>
            ← 返回
          </button>
          <div>
            <h1 className="text-lg font-semibold">OA 中心</h1>
            <p className="text-xs opacity-60">
              {me.user ? `${me.user.nick_name} · ${me.user.dept_name}` : '未录入若依目录'}
              {me.roles.length > 0 && ` · ${me.roles.map((r) => r.role_name).join(' / ')} · 数据范围：${me.data_scope}`}
            </p>
          </div>
          <button className={btnGhost} style={{ ...cardBorder, marginLeft: 'auto' }} onClick={() => loadLists()}>
            刷新
          </button>
        </div>

        {/* Tab（按 perms 显隐） */}
        <div className="mb-4 flex flex-wrap gap-2">
          {tabs.map((t) => (
            <button
              key={t.key}
              className={tab === t.key ? btnPrimary : btnGhost}
              style={tab === t.key ? { background: 'var(--accent)' } : cardBorder}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* 我的待办 */}
        {tab === 'todos' && (
          <div className={`${cardCls} space-y-2`} style={cardBorder}>
            {todos.length === 0 && <Empty text="暂无进行中的 OA 待办" />}
            {todos.map((t) => (
              <div key={t.todo_id} className="rounded-lg border p-3" style={cardBorder}>
                <div className="flex items-center gap-2">
                  <b className="text-base">{t.title}</b>
                  <StatusTag status={t.status} />
                  <span className="ml-auto text-xs opacity-50">
                    {t.assigner_name ? `来自 ${t.assigner_name} · ` : ''}
                    {t.due ? `截止 ${t.due.slice(0, 10)}` : ''}
                  </span>
                </div>
                {t.sub && <p className="mt-1 text-xs opacity-60">{t.sub}</p>}
                {t.status === 'returned' && t.return_reason && (
                  <p className="mt-1 text-xs text-amber-600">退回原因：{t.return_reason}</p>
                )}
                {openId === t.todo_id ? (
                  <div className="mt-2 space-y-2">
                    <textarea
                      className={inputCls} style={cardBorder} rows={2}
                      placeholder="完成说明（可选）" value={opinion}
                      onChange={(e) => setOpinion(e.target.value)}
                    />
                    <div className="flex gap-2">
                      <button className={btnGhost} style={cardBorder} disabled={busy} onClick={() => setOpenId('')}>收起</button>
                      <button
                        className={btnPrimary} style={{ background: 'var(--accent)' }} disabled={busy}
                        onClick={() =>
                          act(() => api('POST', `/oa/todos/${t.todo_id}/submit`, { completion_text: opinion }), '已提交')
                        }
                      >
                        提交完成{t.review_required ? '（待审核）' : ''}
                      </button>
                    </div>
                  </div>
                ) : (
                  <button className="mt-2 text-xs underline opacity-70" onClick={() => { setOpenId(t.todo_id); setOpinion('') }}>
                    处理 / 提交完成
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {/* 待我审批 */}
        {tab === 'approvals' && (
          <div className={`${cardCls} space-y-2`} style={cardBorder}>
            {approvals.length === 0 && <Empty text="暂无待审批的 OA 申请" />}
            {approvals.map((a) => (
              <div key={a.todo_id} className="rounded-lg border p-3" style={cardBorder}>
                <div className="flex items-center gap-2">
                  <b className="text-base">{a.title}</b>
                  <span className="ml-auto text-xs opacity-50">
                    {a.applicant_name ? `${a.applicant_name} 提交 · ` : ''}
                    {a.submitted_at ? new Date(a.submitted_at).toLocaleString('zh-CN', { hour12: false }) : ''}
                  </span>
                </div>
                <div className="mt-2 space-y-1">
                  {payloadRows(a).map(([label, v]) => (
                    <p key={label} className="text-xs opacity-70">
                      <span className="opacity-60">{label}：</span>
                      <span style={{ whiteSpace: 'pre-wrap' }}>{v}</span>
                    </p>
                  ))}
                </div>
                {openId === a.todo_id ? (
                  <div className="mt-2 space-y-2">
                    <textarea
                      className={inputCls} style={cardBorder} rows={2}
                      placeholder="审批意见（退回时必填）" value={opinion}
                      onChange={(e) => setOpinion(e.target.value)}
                    />
                    <div className="flex gap-2">
                      <button className={btnGhost} style={cardBorder} disabled={busy} onClick={() => setOpenId('')}>收起</button>
                      <button
                        className={btnPrimary} style={{ background: 'var(--accent)' }} disabled={busy}
                        onClick={() => {
                          if (!window.confirm(`确认通过「${a.title}」？通过后申请生效，不可撤销。`)) return
                          void act(() => api('POST', `/oa/approvals/${a.todo_id}`, { approve: true, comment: opinion }), '已通过')
                        }}
                      >
                        通过
                      </button>
                      <button
                        className={btnGhost} style={cardBorder}
                        disabled={busy || !opinion.trim()}
                        onClick={() => {
                          if (!window.confirm(`确认退回「${a.title}」？将退回申请人修改后重新提交。`)) return
                          void act(() => api('POST', `/oa/approvals/${a.todo_id}`, { approve: false, comment: opinion }), '已退回')
                        }}
                      >
                        退回
                      </button>
                    </div>
                  </div>
                ) : (
                  <button className="mt-2 text-xs underline opacity-70" onClick={() => { setOpenId(a.todo_id); setOpinion('') }}>
                    审批
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {/* 我发起的 */}
        {tab === 'requests' && (
          <div className={`${cardCls} space-y-2`} style={cardBorder}>
            {requests.length === 0 && <Empty text="还没有发起过 OA 申请" />}
            {requests.map((r) => (
              <div key={r.todo_id} className="rounded-lg border p-3" style={cardBorder}>
                <div className="flex items-center gap-2">
                  <b className="text-base">{r.title}</b>
                  <StatusTag status={r.status} />
                  <span className="ml-auto text-xs opacity-50">
                    {r.dispatcher_name ? `审批人 ${r.dispatcher_name}` : ''}
                  </span>
                </div>
                <div className="mt-2 space-y-1">
                  {payloadRows(r).map(([label, v]) => (
                    <p key={label} className="text-xs opacity-70">
                      <span className="opacity-60">{label}：</span>
                      <span style={{ whiteSpace: 'pre-wrap' }}>{v}</span>
                    </p>
                  ))}
                </div>
                {r.status === 'returned' && r.return_reason && (
                  <p className="mt-1 text-xs text-amber-600">退回原因：{r.return_reason}（可修改后在对话中重提）</p>
                )}
                {r.status === 'returned' && openId === r.todo_id && (
                  <div className="mt-2 space-y-2">
                    <TemplateForm
                      tpl={{ form: r.form ?? { fields: [] } } as OaTemplate}
                      values={{ ...(r.payload ?? {}) }}
                      set={(k, v) =>
                        setFormVals((prev) => ({ ...prev, [`${r.todo_id}:${k}`]: v }))
                      }
                    />
                    <button
                      className={btnPrimary} style={{ background: 'var(--accent)' }} disabled={busy}
                      onClick={() => {
                        const merged = { ...(r.payload ?? {}) }
                        Object.entries(formVals).forEach(([key, v]) => {
                          if (key.startsWith(`${r.todo_id}:`)) merged[key.split(':')[1]] = v
                        })
                        act(() => api('POST', `/oa/todos/${r.todo_id}/submit`, { payload: merged }), '已重新提交')
                      }}
                    >
                      重新提交
                    </button>
                  </div>
                )}
                {r.status === 'returned' && openId !== r.todo_id && (
                  <button className="mt-2 text-xs underline opacity-70" onClick={() => { setOpenId(r.todo_id); setFormVals({}) }}>
                    修改并重提
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {/* 发起申请 */}
        {tab === 'new-request' && (
          <div className={`${cardCls} space-y-3`} style={cardBorder}>
            <div className="flex flex-wrap gap-2">
              {templates.map((t) => (
                <button
                  key={t.template_key}
                  className={tplKey === t.template_key ? btnPrimary : btnGhost}
                  style={tplKey === t.template_key ? { background: 'var(--accent)' } : cardBorder}
                  onClick={() => setTplKey(t.template_key)}
                >
                  {t.name}
                </button>
              ))}
            </div>
            {tpl && (
              <>
                <p className="text-xs opacity-50">
                  提交后自动送审（审批角色：{tpl.approver_role === 'finance' ? '财务审批' : '部门主管'}）
                </p>
                <TemplateForm tpl={tpl} values={formVals} set={(k, v) => setFormVals((prev) => ({ ...prev, [k]: v }))} />
                {reqMissing.length > 0 && (
                  <p className="text-xs text-amber-600">必填项待补：{reqMissing.map((f) => f.label).join('、')}</p>
                )}
                <button
                  className={btnPrimary} style={{ background: 'var(--accent)' }}
                  disabled={busy || reqMissing.length > 0}
                  onClick={async () => {
                    await act(
                      () => api('POST', '/oa/requests', { template_key: tplKey, form_values: formVals }),
                      '申请已提交，等待审批',
                    )
                    setTab('requests')
                  }}
                >
                  提交申请
                </button>
              </>
            )}
          </div>
        )}

        {/* 派发任务 */}
        {tab === 'new-task' && (
          <div className={`${cardCls} space-y-3`} style={cardBorder}>
            <label className="block">
              <span className="mb-1 block text-xs opacity-60">任务标题 *</span>
              <input className={inputCls} style={cardBorder} value={taskTitle}
                onChange={(e) => setTaskTitle(e.target.value)} placeholder="如：整理季度经营数据" />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs opacity-60">要求说明</span>
              <textarea className={inputCls} style={cardBorder} rows={2} value={taskNote}
                onChange={(e) => setTaskNote(e.target.value)} />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs opacity-60">截止日期</span>
              <input className={inputCls} style={cardBorder} type="date" value={taskDue}
                onChange={(e) => setTaskDue(e.target.value)} />
            </label>
            <div>
              <span className="mb-1 block text-xs opacity-60">
                承办人 *（数据范围：{me.data_scope}；已选 {picked.size} 人）
              </span>
              <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
                {directory.map((u) => (
                  <label key={u.user_id} className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs" style={cardBorder}>
                    <input
                      type="checkbox"
                      checked={picked.has(u.user_id)}
                      onChange={() => {
                        const n = new Set(picked)
                        if (n.has(u.user_id)) n.delete(u.user_id)
                        else n.add(u.user_id)
                        setPicked(n)
                      }}
                    />
                    <span>{u.nick_name}</span>
                    <span className="ml-auto opacity-50">{u.dept_name}</span>
                  </label>
                ))}
              </div>
            </div>
            <button
              className={btnPrimary} style={{ background: 'var(--accent)' }}
              disabled={busy || !taskTitle.trim() || picked.size === 0}
              onClick={async () => {
                await act(
                  () =>
                    api('POST', '/oa/tasks', {
                      title: taskTitle,
                      assignee_ids: [...picked],
                      note: taskNote,
                      due: taskDue || null,
                    }),
                  '任务已派发',
                )
                setTaskTitle(''); setTaskNote(''); setTaskDue(''); setPicked(new Set())
                setTab('todos')
              }}
            >
              确认派发
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
