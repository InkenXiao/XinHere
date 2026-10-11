import { useState } from 'react'
import type { ParseFieldDef, TemplateTab } from '@contracts/events'
import { TemplateTabs } from './TemplateTabs'

const controlClass = 'w-full min-w-0 rounded-xl border border-slate-200 bg-white px-3.5! py-2.5! text-base text-slate-800 shadow-sm outline-none transition focus:border-sky-400/60 focus:ring-3 focus:ring-sky-400/15 disabled:opacity-60'

const empty = (value: unknown) => value == null || String(value).trim() === ''

function normalizeInitial(tabs: TemplateTab[], columns: ParseFieldDef[], initial: Record<string, unknown>) {
  const next = { ...initial }
  for (const tab of tabs.filter((item) => item.presentation === 'grid')) {
    for (const field of columns.filter((item) => tab.field_keys.includes(item.key))) {
      const value = next[field.key]
      next[field.key] = Array.isArray(value) ? value : [value ?? null]
    }
  }
  return next
}

export function TabbedFillForm({ tabs, columns, initial, busy, readOnly = false, onSaveDraft, onSubmit }: {
  tabs: TemplateTab[]
  columns: ParseFieldDef[]
  initial: Record<string, unknown>
  busy: boolean
  readOnly?: boolean
  onSaveDraft: (data: Record<string, unknown>) => void
  onSubmit: (data: Record<string, unknown>) => void
}) {
  const [active, setActive] = useState(tabs[0].id)
  const [activeRow, setActiveRow] = useState(0)
  const [values, setValues] = useState(() => normalizeInitial(tabs, columns, initial))
  const [error, setError] = useState('')
  const tab = tabs.find((item) => item.id === active) ?? tabs[0]
  const fields = columns.filter((field) => tab.field_keys.includes(field.key))
  const filledCount = columns.filter((field) => Array.isArray(values[field.key]) ? (values[field.key] as unknown[]).some((value) => !empty(value)) : !empty(values[field.key])).length
  const progress = columns.length ? Math.round((filledCount / columns.length) * 100) : 0
  const rowCount = tab.presentation === 'grid' ? Math.max(1, ...fields.map((field) => Array.isArray(values[field.key]) ? (values[field.key] as unknown[]).length : 1)) : 1
  const payload = () => Object.fromEntries(columns.map((field) => [field.key, values[field.key] ?? null]))

  const submit = () => {
    if (busy || readOnly) return
    // 校验全部分页，定位到第一个缺项，避免只提交当前可见页。
    const missing = columns.find((field) => {
      if (!field.required) return false
      const page = tabs.find((item) => item.field_keys.includes(field.key))
      const value = values[field.key]
      return page?.presentation === 'grid'
        ? !Array.isArray(value) || value.length === 0 || value.some(empty)
        : empty(value)
    })
    if (missing) {
      const page = tabs.find((item) => item.field_keys.includes(missing.key))!
      setActive(page.id)
      setError(`请填写「${page.name}」中的「${missing.label}」`)
      return
    }
    setError('')
    onSubmit(payload())
  }

  const control = (field: ParseFieldDef, value: unknown, change: (value: string) => void, id = `field-${field.key}`) => {
    const props = {
      id,
      'aria-label': field.label,
      'aria-required': field.required,
      className: controlClass,
      disabled: busy || readOnly,
      value: String(value ?? ''),
    }
    if (field.type === 'select') return <select {...props} onChange={(e) => change(e.target.value)}><option value="">请选择</option>{field.options?.map((option) => <option key={option} value={option}>{option}</option>)}</select>
    if (field.type === 'textarea') return <textarea {...props} rows={3} onChange={(e) => change(e.target.value)} />
    return <input {...props} type={field.type === 'number' || field.type === 'date' ? field.type : 'text'} step={field.type === 'number' ? 'any' : undefined} onChange={(e) => change(e.target.value)} />
  }

  const parseValue = (field: ParseFieldDef, value: string) => value === '' ? null : field.type === 'number' ? Number(value) : value
  const setScalar = (field: ParseFieldDef, value: string) => setValues((previous) => ({ ...previous, [field.key]: parseValue(field, value) }))
  const setCell = (field: ParseFieldDef, row: number, value: string) => setValues((previous) => {
    const items = Array.isArray(previous[field.key]) ? [...previous[field.key] as unknown[]] : [previous[field.key] ?? null]
    while (items.length < rowCount) items.push(null)
    items[row] = parseValue(field, value)
    return { ...previous, [field.key]: items }
  })
  const addRow = () => {
    setValues((previous) => Object.fromEntries(Object.entries(previous).map(([key, value]) => [key, tab.field_keys.includes(key) ? [...(Array.isArray(value) ? value : [value ?? null]), null] : value])))
    setActiveRow(rowCount)
  }
  const deleteRow = (row: number) => setValues((previous) => Object.fromEntries(Object.entries(previous).map(([key, value]) => [key, tab.field_keys.includes(key) && Array.isArray(value) ? value.filter((_, index) => index !== row) : value])))

  return (
    <form onSubmit={(e) => { e.preventDefault(); submit() }} className="mt-4! overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="grid lg:grid-cols-[220px_minmax(0,1fr)]">
        <aside className="border-b border-slate-200 bg-slate-50 p-3! lg:border-r lg:border-b-0 lg:p-4!">
          <div className="mb-4! hidden lg:block">
            <div className="mb-2! flex items-center justify-between text-xs text-[var(--ink-50)]"><span>整体进度</span><b className="text-[var(--ink-100)]">{progress}%</b></div>
            <div className="h-1.5 overflow-hidden rounded-full bg-black/10"><div className="h-full rounded-full bg-sky-500 transition-[width]" style={{ width: `${progress}%` }} /></div>
            <p className="mt-2! text-xs text-[var(--ink-50)]">已填写 {filledCount}/{columns.length} 项</p>
          </div>
          <TemplateTabs tabs={tabs} active={active} onChange={(id) => { setActive(id); setError('') }} />
        </aside>
        <section className="min-w-0 p-5! sm:p-7! lg:p-10!">
          <div className="mb-7! border-b border-[var(--card-border)] pb-5!">
            <div className="mb-2! flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <span className="rounded-full bg-sky-500/10 px-2.5! py-1! font-medium text-sky-600">第 {tabs.findIndex((item) => item.id === active) + 1} 页 / 共 {tabs.length} 页</span>
              <span className="truncate">{tab.source_file_name}</span>
            </div>
            <h3 className="text-lg font-semibold tracking-tight text-slate-900">{tab.title || tab.name}</h3>
            {tab.metadata?.map((line, i) => <p key={i} className="mt-2! whitespace-pre-wrap text-base leading-6 text-slate-600">{line}</p>)}
          </div>
          {tab.presentation === 'grid' ? (
        <div>
        <div className="rounded-2xl border border-slate-200 bg-slate-50/60 p-4! sm:p-5!">
          <div className="mb-5! flex min-w-0 items-center gap-3 border-b border-slate-200 pb-3!">
            <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto pb-1!">{Array.from({ length: rowCount }, (_, row) => <div key={row} className={`flex shrink-0 items-center rounded-lg text-base ${activeRow === row ? 'bg-sky-500 text-white' : 'bg-white text-slate-500'}`}><button type="button" onClick={() => setActiveRow(row)} className="px-4! py-2!">第 {row + 1} 条</button>{rowCount > 1 && <button type="button" aria-label={`删除第 ${row + 1} 条记录`} disabled={busy || readOnly} onClick={() => deleteRow(row)} className={`px-2! text-lg ${activeRow === row ? 'text-white/80 hover:text-white' : 'text-slate-400 hover:text-rose-500'} disabled:opacity-30`}>×</button>}</div>)}</div>
            <button type="button" aria-label="新增一条记录" title="新增一条记录" className="grid size-10 shrink-0 place-items-center rounded-xl border border-sky-300 bg-sky-500 text-2xl text-white shadow-sm transition-colors hover:bg-sky-600 disabled:opacity-40" disabled={busy || readOnly} onClick={addRow}>+</button>
          </div>
          <div className="grid gap-x-6 gap-y-5 sm:grid-cols-2">{fields.map((field) => { const items = Array.isArray(values[field.key]) ? values[field.key] as unknown[] : [values[field.key]]; return <div key={field.key} className={field.type === 'textarea' ? 'sm:col-span-2' : ''}><label className="mb-2! block text-base font-medium text-[var(--ink-70)]" htmlFor={`field-${field.key}-${activeRow}`}>{field.label}{field.required && <span className="ml-1! text-rose-500">*</span>}</label>{control(field, items[activeRow], (value) => setCell(field, activeRow, value), `field-${field.key}-${activeRow}`)}</div> })}</div>
          <table className="hidden w-full table-fixed border-collapse text-base" style={{ minWidth: fields.reduce((sum, field) => sum + (field.width ?? 180), 0) }}>
            <colgroup><col style={{ width: 64 }} />{fields.map((field) => <col key={field.key} style={{ width: field.width ?? 160 }} />)}<col style={{ width: 72 }} /></colgroup>
            <thead><tr><th className="border-b border-r border-[var(--card-border)] bg-black/[0.025] p-3.5! text-center font-medium text-[var(--ink-50)]">序号</th>{fields.map((field) => <th key={field.key} className="border-b border-r border-[var(--card-border)] bg-black/[0.025] p-3.5! text-left font-medium text-[var(--ink-70)]"><label htmlFor={`field-${field.key}-0`}>{field.label}{field.required && <span className="text-rose-500"> *</span>}</label></th>)}<th className="border-b border-[var(--card-border)] bg-black/[0.025] p-3! text-center text-xs font-medium text-[var(--ink-50)]">操作</th></tr></thead>
            <tbody>{Array.from({ length: rowCount }, (_, row) => <tr key={row} className="border-b border-[var(--card-border)] last:border-b-0"><td className="border-r border-[var(--card-border)] p-3! text-center text-xs font-medium text-[var(--ink-50)]">{String(row + 1).padStart(2, '0')}</td>{fields.map((field) => {
              const items = Array.isArray(values[field.key]) ? values[field.key] as unknown[] : [values[field.key]]
              return <td key={field.key} className="border-r border-[var(--card-border)] p-3! align-top">{control(field, items[row], (value) => setCell(field, row, value), `field-${field.key}-${row}`)}</td>
            })}<td className="p-3! text-center"><button type="button" className="rounded-lg px-2! py-1.5! text-xs text-rose-400 transition-colors hover:bg-rose-500/10 disabled:cursor-not-allowed disabled:opacity-30" disabled={busy || readOnly || rowCount === 1} onClick={() => deleteRow(row)}>删除</button></td></tr>)}</tbody>
          </table>
        </div>
          <div className="hidden mt-3! flex items-center justify-between gap-3"><span>共 {rowCount} 条记录</span></div>
        </div>
      ) : (
        <div className="grid gap-x-6 gap-y-5 sm:grid-cols-2">{fields.map((field) => <div key={field.key} className={field.type === 'textarea' ? 'sm:col-span-2' : ''}>
          <label className="mb-2! block text-base font-medium text-[var(--ink-70)]" htmlFor={`field-${field.key}`}>{field.label}{field.required && <span className="ml-1! text-rose-500">*</span>}</label>
          {control(field, values[field.key], (value) => setScalar(field, value))}
        </div>)}</div>
      )}
          {error && <p role="alert" className="mt-5! rounded-xl border border-rose-300/50 bg-rose-500/8 px-4! py-3! text-base text-rose-600">{error}</p>}
        </section>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--card-border)] bg-[var(--pop-bg)] px-4! py-4! sm:px-6!">
        <span className="text-xs text-[var(--ink-50)]">{readOnly ? '本次填报已完成，内容已锁定' : '内容会在切换页面时保留，全部填写完成后统一提交'}</span>
        <div className="flex flex-wrap justify-end gap-2">
          {!readOnly && <button type="button" className="btn-ghost min-w-28 px-5! py-2.5!" disabled={busy} onClick={() => onSaveDraft(payload())}>{busy ? '保存中…' : '保存草稿'}</button>}
          <button type="submit" className="btn-primary min-w-32 px-5! py-2.5!" disabled={busy || readOnly}>{readOnly ? '已完成' : busy ? '处理中…' : '提交全部填报'}</button>
        </div>
      </div>
    </form>
  )
}
