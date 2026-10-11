import { useEffect, useState } from 'react'
import type { TaskAnalytics } from '@contracts/events'
import { api } from '@/scms/api'
import ScmsModal from '@/scms/common/ScmsModal'
import { TODO_STATUS_ZH } from '@/utils'
import { fmtTime } from '@/utils'
import { TemplateTabs } from './TemplateTabs'

export function DataAnalyticsModal({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const [data, setData] = useState<TaskAnalytics | null>(null)
  const [err, setErr] = useState('')
  const [activeTab, setActiveTab] = useState('')
  const [operations, setOperations] = useState<Record<string, 'sum' | 'join'>>({})
  const [viewMode, setViewMode] = useState<'summary' | 'parallel'>('summary')
  const [exportMenuOpen, setExportMenuOpen] = useState(false)

  useEffect(() => {
    api<TaskAnalytics>('GET', `/tasks/${taskId}/analytics`, undefined, { silent: true })
      .then((r) => { setData(r.data); setActiveTab(r.data.tabs?.[0]?.id ?? '') })
      .catch((e: Error) => setErr(e.message || '数据分析加载失败'))
  }, [taskId])

  const rate = data?.total ? Math.round(data.submitted / data.total * 100) : 0
  const tab = data?.tabs?.find((item) => item.id === activeTab)
  const fields = (data?.fields ?? []).filter((field) => !tab || tab.field_keys.includes(field.key))

  const exportSummary = async () => {
    if (!data) return
    const XLSX = await import('xlsx')
    const rows: (string | number)[][] = [
      ['数据字段', ...data.records.map((record) => record.assignee_name || record.assignee_id), '操作', '汇总'],
      ...fields.map((field) => {
        const operation = operations[activeTab + ':' + field.key] ?? 'join'
        return [field.label, ...data.records.map((record) => formatValue(record.data?.[field.key])), operation === 'sum' ? '数值累加' : '项目排列', summaryForField(field, data.records, operation)]
      }),
    ]
    const sheet = XLSX.utils.aoa_to_sheet(rows)
    sheet['!cols'] = [{ wch: 24 }, ...data.records.map(() => ({ wch: 20 })), { wch: 14 }, { wch: 36 }]
    const book = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(book, sheet, (tab?.name || '汇总数据').slice(0, 31))
    const safeName = `${data.task_title}-${tab?.name || '汇总数据'}`.replace(/[\\/:*?"<>|]/g, '_')
    XLSX.writeFile(book, `${safeName}.xlsx`)
  }

  const exportAll = async () => {
    if (!data) return
    const [XLSX, JSZip] = await Promise.all([import('xlsx'), import('jszip')])
    const zip = new JSZip.default()
    const tabs = data.tabs?.length ? data.tabs : [{ id: 'all', name: '汇总数据', field_keys: data.fields.map((field) => field.key) }]
    for (const item of tabs) {
      const tabFields = data.fields.filter((field) => item.field_keys.includes(field.key))
      const rows: (string | number)[][] = [['数据字段', ...data.records.map((record) => record.assignee_name || record.assignee_id), '操作', '汇总'], ...tabFields.map((field) => [field.label, ...data.records.map((record) => formatValue(record.data?.[field.key])), '项目排列', summaryForField(field, data.records, 'join')])]
      const sheet = XLSX.utils.aoa_to_sheet(rows)
      const book = XLSX.utils.book_new()
      XLSX.utils.book_append_sheet(book, sheet, item.name.slice(0, 31))
      const bytes = XLSX.write(book, { bookType: 'xlsx', type: 'array' })
      const fileName = `${data.task_title}-${item.name}`.replace(/[\\/:*?"<>|]/g, '_')
      zip.file(`${fileName}.xlsx`, bytes)
    }
    const blob = await zip.generateAsync({ type: 'blob' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${data.task_title}-全部结果表.zip`.replace(/[\\/:*?"<>|]/g, '_')
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <ScmsModal title="数据分析" onClose={onClose} centered width="min(1500px, 98vw)" bodyMaxHeight="84vh" light>
      {err ? <div className="tcc-err">{err}</div> : !data ? <div className="tcc-note-txt">正在分析填报数据…</div> : (
        <div className="max-h-[78vh] space-y-7 overflow-y-auto bg-white pr-2! text-slate-700 sm:space-y-8">
          <section className="relative min-h-36 overflow-hidden rounded-2xl border border-sky-200 bg-sky-50 p-6! shadow-sm sm:p-7!">
            <div className="absolute -right-10 -top-14 h-40 w-40 rounded-full bg-cyan-400/10 blur-2xl" />
            <div className="relative flex flex-wrap items-start justify-between gap-6">
              <div className="min-w-0 flex-1"><div className="text-xs tracking-[.2em] text-[var(--accent)]">FILLING INSIGHTS</div><h3 className="mt-2! text-xl font-semibold leading-8 text-[var(--ink-100)] sm:text-2xl">{data.task_title}</h3><p className="mt-2! text-base leading-6 text-[var(--ink-50)]">数据来源模板：{data.template_name}</p></div>
              <div className="min-w-28 rounded-2xl border border-sky-400/20 bg-[var(--pop-bg)]/50 p-4! text-center"><strong className="block text-3xl leading-none text-[var(--accent-strong)]">{rate}%</strong><div className="mt-2! text-xs text-[var(--ink-50)]">已填报 {data.submitted}/{data.total}</div></div>
            </div>
          </section>

          <section className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
            <Metric label="派发人数" value={data.total} tone="sky" />
            <Metric label="已填写" value={data.submitted} tone="cyan" />
            <Metric label="已完成" value={data.completed} tone="emerald" />
            <Metric label="待填写" value={Math.max(0, data.total - data.submitted)} tone="amber" />
          </section>

          <section>
            <div className="mb-4! flex items-end justify-between gap-3"><div><h4 className="text-lg font-semibold text-[var(--ink-100)]">填写人基本记录</h4><p className="mt-1! text-xs text-[var(--ink-50)]">查看每位办理人的填写完整度与提交时间</p></div><span className="text-xs text-[var(--ink-50)]">共 {data.records.length} 条</span></div>
            <div className="overflow-hidden rounded-2xl border border-[var(--glass-bd)] bg-[var(--glass)]">
              <div className="hidden grid-cols-[1.1fr_.8fr_.7fr_1fr] gap-4 bg-[var(--glass-2)] px-5! py-3.5! text-xs text-[var(--ink-50)] sm:grid"><span>填写人</span><span>状态</span><span>字段完成度</span><span>提交时间</span></div>
              {data.records.map((record) => (
                <div key={record.todo_id} className="grid gap-3 border-t border-[var(--glass-bd)] px-5! py-4! text-base first:border-t-0 sm:grid-cols-[1.1fr_.8fr_.7fr_1fr] sm:items-center sm:gap-4">
                  <div className="flex items-center justify-between gap-3"><strong className="truncate text-[var(--ink-100)]">{record.assignee_name || record.assignee_id}</strong><span className="rounded-full bg-sky-500/10 px-2 py-1 text-xs text-sky-600 sm:hidden">{TODO_STATUS_ZH[record.status as keyof typeof TODO_STATUS_ZH] ?? record.status}</span></div>
                  <span className="hidden sm:inline">{TODO_STATUS_ZH[record.status as keyof typeof TODO_STATUS_ZH] ?? record.status}</span>
                  <span className="text-xs text-[var(--ink-50)]">已填写字段 {record.filled_fields}/{record.field_total}</span>
                  <span className="text-xs text-[var(--ink-50)]">{record.submitted_at ? fmtTime(record.submitted_at) : '尚未提交'}</span>
                </div>
              ))}
            </div>
          </section>

          <section>
            <div className="mb-4!"><h4 className="text-lg font-semibold text-[var(--ink-100)]">填写字段统计</h4><p className="mt-1! text-xs text-[var(--ink-50)]">按表单分页查看字段填写率与数据分布</p></div>
            {!!data.tabs?.length && <div className="mb-4! min-w-0"><TemplateTabs tabs={data.tabs} active={activeTab} onChange={setActiveTab} layout="tabs" prominentScrollbar /></div>}
            <div className="mb-5! flex w-full flex-wrap items-center justify-end gap-2 border-y border-slate-100 bg-slate-50/70 px-3! py-3! sm:px-4!">
              {viewMode === 'summary' && <div className="relative">
                <button type="button" aria-expanded={exportMenuOpen} className="rounded-xl border border-emerald-300 bg-white px-5! py-2.5! text-sm font-semibold text-emerald-700 shadow-sm transition-colors hover:bg-emerald-50" onClick={() => setExportMenuOpen((open) => !open)}>导出 Excel</button>
                {exportMenuOpen && <>
                  <button type="button" aria-label="关闭导出菜单" className="fixed inset-0 z-[9] cursor-default" onClick={() => setExportMenuOpen(false)} />
                  <div className="absolute right-0 top-full z-10 mt-2 min-w-32 overflow-hidden rounded-xl border border-slate-200 bg-white p-1 shadow-lg">
                    <button type="button" className="block w-full rounded-lg px-3! py-2! text-left text-sm text-slate-700 hover:bg-emerald-50" onClick={() => { setExportMenuOpen(false); void exportSummary() }}>导出当前</button>
                    <button type="button" className="block w-full rounded-lg px-3! py-2! text-left text-sm text-slate-700 hover:bg-emerald-50" onClick={() => { setExportMenuOpen(false); void exportAll() }}>全部导出</button>
                  </div>
                </>}
              </div>}
              <button type="button" className="rounded-xl border border-sky-300 bg-white px-5! py-2.5! text-sm font-semibold text-sky-700 shadow-sm transition-colors hover:bg-sky-50" onClick={() => setViewMode((mode) => mode === 'summary' ? 'parallel' : 'summary')}>{viewMode === 'summary' ? '项目并列' : '返回汇总表'}</button>
              <button type="button" disabled className="cursor-not-allowed rounded-xl border border-slate-200 bg-slate-100 px-5! py-2.5! text-sm font-semibold text-slate-400 shadow-sm">归档到知识库（实现中）</button>
            </div>
            {viewMode === 'parallel' && <div className="grid gap-5 xl:grid-cols-2">{data.records.map((record) => <article key={record.todo_id} className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"><header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-slate-50 px-5! py-4!"><div><h5 className="font-semibold text-slate-900">{record.assignee_name || record.assignee_id}</h5><p className="mt-1! text-xs text-slate-500">{TODO_STATUS_ZH[record.status as keyof typeof TODO_STATUS_ZH] ?? record.status} · {record.submitted_at ? fmtTime(record.submitted_at) : '尚未提交'}</p></div><span className="rounded-full bg-sky-100 px-3! py-1.5! text-xs font-medium text-sky-700">已填写 {record.filled_fields}/{record.field_total}</span></header><div className="grid gap-x-6 gap-y-5 p-5! sm:grid-cols-2">{fields.map((field) => <div key={field.key} className="min-w-0"><div className="mb-2! text-xs font-medium text-slate-500">{field.label}</div><div className="min-h-11 rounded-xl border border-slate-200 bg-slate-50 px-4! py-3! text-base font-medium text-slate-800">{formatValue(record.data?.[field.key])}</div></div>)}</div></article>)}</div>}
            {viewMode === 'summary' && <>
            <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white"><table className="w-full min-w-[980px] border-collapse text-base"><thead><tr className="bg-slate-50 text-left text-xs font-semibold text-slate-600"><th className="sticky left-0 border-b border-r border-slate-200 bg-slate-50 px-4! py-3!">数据字段</th>{data.records.map((record) => <th key={record.todo_id} className="border-b border-slate-200 px-4! py-3!">{record.assignee_name || record.assignee_id}</th>)}<th className="border-b border-slate-200 px-4! py-3!">操作</th><th className="border-b border-slate-200 px-4! py-3!">汇总</th></tr></thead><tbody>{fields.map((field) => { const operationKey = activeTab + ':' + field.key; const operation = operations[operationKey] ?? 'join'; return <tr key={field.key} className="border-b border-slate-100 text-slate-700"><td className="sticky left-0 border-r border-slate-200 bg-white px-4! py-3! font-semibold text-slate-900">{field.label}</td>{data.records.map((record) => <td key={record.todo_id} className="px-4! py-3!">{formatValue(record.data?.[field.key])}</td>)}<td className="px-4! py-3!"><select className="rounded-lg border border-slate-200 bg-white px-2! py-1.5! text-xs" value={operation} onChange={(e) => setOperations((prev) => ({ ...prev, [operationKey]: e.target.value as 'sum' | 'join' }))}><option value="sum">数值累加</option><option value="join">项目排列</option></select></td><td className="px-4! py-3! font-semibold text-sky-700">{summaryForField(field, data.records, operation)}</td></tr> })}</tbody></table></div>
            <div className="hidden grid gap-4 sm:gap-5 md:grid-cols-2">
              {fields.map((field) => {
                const percent = field.total ? Math.round(field.filled / field.total * 100) : 0
                const top = Object.entries(field.options).slice(0, 3)
                return <div key={field.key} className="min-h-32 rounded-2xl border border-[var(--glass-bd)] bg-[var(--glass)] p-5! shadow-sm transition-transform hover:-translate-y-0.5">
                  <div className="flex items-center justify-between gap-3"><strong className="text-base text-[var(--ink-100)]">{field.label}</strong><span className="text-xs text-[var(--accent)]">填写率 {percent}%</span></div>
                  <div className="mt-4! h-2 overflow-hidden rounded-full bg-[rgba(126,150,166,.2)]"><div className="h-full rounded-full bg-gradient-to-r from-sky-500 to-emerald-400" style={{ width: `${percent}%` }} /></div>
                  <div className="mt-4! flex flex-wrap gap-2 text-xs leading-5 text-[var(--ink-50)]">
                    <span>{field.filled}/{field.total} 人填写</span>
                    {field.type === 'number' && <span>· 数值合计 {formatNumber(field.number_total)}</span>}
                  </div>
                  {top.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{top.map(([label, count]) => <span key={label} className="rounded-full border border-sky-400/20 bg-sky-500/10 px-2.5 py-1 text-xs text-[var(--ink-70)]">{label} · {Number(count)}</span>)}</div>}
                </div>
              })}
            </div>
            </>}
          </section>
        </div>
      )}
    </ScmsModal>
  )
}

function Metric({ label, value, tone }: { label: string; value: number; tone: 'sky' | 'cyan' | 'emerald' | 'amber' }) {
  const colors = { sky: 'border-sky-400/25 bg-sky-500/10', cyan: 'border-cyan-400/25 bg-cyan-500/10', emerald: 'border-emerald-400/25 bg-emerald-500/10', amber: 'border-amber-400/25 bg-amber-500/10' }
  return <div className={`min-h-24 rounded-2xl border p-5! ${colors[tone]}`}><div className="text-xs text-[var(--ink-50)]">{label}</div><strong className="mt-3! block text-3xl tabular-nums leading-none text-[var(--ink-100)]">{value}</strong></div>
}

function formatNumber(value: number) {
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.filter((item) => item !== null && item !== '').map(String).join('、') || '—'
  return String(value)
}

export function summaryForRecord(record: TaskAnalytics['records'][number], fields: TaskAnalytics['fields'], operation: 'sum' | 'join'): string {
  const values = fields.flatMap((field) => {
    const value = record.data?.[field.key]
    return Array.isArray(value) ? value : [value]
  }).filter((value) => value !== null && value !== undefined && value !== '')
  if (operation === 'sum') {
    const numbers = values.map(Number).filter((value) => Number.isFinite(value))
    return numbers.length ? formatNumber(numbers.reduce((total, value) => total + value, 0)) : '—'
  }
  return values.map(String).join('、') || '—'
}

function summaryForField(field: TaskAnalytics['fields'][number], records: TaskAnalytics['records'], operation: 'sum' | 'join'): string {
  const values = records.flatMap((record) => { const value = record.data?.[field.key]; return Array.isArray(value) ? value : [value] }).filter((value) => value !== null && value !== undefined && value !== '')
  if (operation === 'sum') {
    const numbers = values.map(Number).filter(Number.isFinite)
    return numbers.length ? formatNumber(numbers.reduce((total, value) => total + value, 0)) : '—'
  }
  return values.map(String).join('、') || '—'
}
