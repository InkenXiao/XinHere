// 动态模板创建弹窗（三步）：① 选 Excel 解析 → ② 字段调整（类型/必填/选项）→ ③ 命名保存。
// 创建即启用（status=active），立即出现在派发下拉动态组（dyn_ 前缀）。
import { useEffect, useRef, useState } from 'react'
import { Select } from 'antd'
import { useUiStore } from '@/scms/uiShim'
import { useTodoStore } from '@/scms/todoShim'
import { api } from '@/scms/api'
import ScmsModal from '@/scms/common/ScmsModal'
import DirectoryPicker, {
  type PickTarget,
} from '@/scms/common/DirectoryPicker'
import {
  createFromExcels,
  isExcelFile,
  listTemplates,
  parseExcels,
} from '../api'
import { TemplateTabs } from './TemplateTabs'
import {
  FIELD_TYPES,
  FIELD_TYPE_ZH,
  type DynamicTemplateItem,
  type ParseFieldDef,
  type ParseResult,
} from '../types'
import type { ExpandResult } from '@/scms/types'

const inputCls =
  'w-full rounded-md border px-2.5 py-1.5 text-base bg-transparent outline-none focus:ring-1'
const inputStyle = { borderColor: 'var(--card-border)' }

const STEPS = ['上传 Excel', '调整字段', '命名保存', '立即派发']

export function TemplateCreateModal({ onClose }: { onClose: () => void }) {
  const toast = useUiStore((s) => s.toast)
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  // Step1：解析结果 + 原始文件（保存时随 FormData 重传）
  const [files, setFiles] = useState<File[]>([])
  const [activeTab, setActiveTab] = useState('')
  const [parse, setParse] = useState<ParseResult | null>(null)
  // Step2：字段调整副本（label/type/required/options）
  const [cols, setCols] = useState<ParseFieldDef[]>([])
  // Step3：模板名（默认文件名去后缀）
  const [name, setName] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [targetQuery, setTargetQuery] = useState('')
  const [targetOpen, setTargetOpen] = useState(false)
  const [targets, setTargets] = useState<PickTarget[]>([])
  const [assignees, setAssignees] = useState<string[]>([])
  const [assigneeLabels, setAssigneeLabels] = useState<string[]>([])
  const [period, setPeriod] = useState(
    new Date().toLocaleDateString('sv-SE').slice(0, 7),
  )
  const [due, setDue] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyBusy, setHistoryBusy] = useState(false)
  const [templates, setTemplates] = useState<DynamicTemplateItem[]>([])
  const [usingHistory, setUsingHistory] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const currentTab = parse?.tabs?.find((tab) => tab.id === activeTab)
  const currentColumns = cols
    .map((c, i) => ({ c, i }))
    .filter(({ c }) => currentTab?.field_keys.includes(c.key))
  const sampleRows = currentTab?.sample_rows ?? []

  useEffect(() => {
    if (!targets.length) {
      setAssignees([])
      setAssigneeLabels([])
      return
    }
    api<ExpandResult>(
      'POST',
      '/org/expand',
      {
        targets: targets.map((target) => ({
          type: target.type,
          id: target.e.id,
        })),
      },
      { silent: true },
    )
      .then((r) => {
        setAssignees([...new Set(r.data.user_ids)])
        setAssigneeLabels(r.data.labels)
      })
      .catch(() => undefined)
  }, [targets])

  const onPick = async (picked: File[]) => {
    setErr('')
    if (picked.some((f) => !isExcelFile(f))) {
      setErr('仅支持 .xls / .xlsx / .xlsm 格式的 Excel 文件')
      return
    }
    if (
      !picked.length ||
      picked.length > 10 ||
      picked.some((f) => f.size > 10 * 1024 * 1024) ||
      picked.reduce((sum, f) => sum + f.size, 0) > 30 * 1024 * 1024
    ) {
      setErr('请选择 1 至 10 个 Excel，单个不超过 10MB，合计不超过 30MB')
      return
    }
    setBusy(true)
    try {
      const r = await parseExcels(picked)
      setFiles(picked)
      setUsingHistory(false)
      setParse(r)
      setActiveTab(r.tabs?.[0]?.id ?? '')
      setCols(r.columns.map((c) => ({ ...c })))
      setName(
        picked[0].name.replace(/\.(xls|xlsx|xlsm)$/i, '') +
          (picked.length > 1 ? '等信息填报' : ''),
      )
      setStep(2)
    } catch (e) {
      // 解析失败（空表头/无数据行等）内联展示，不弹全局 toast
      setErr((e as Error).message || '解析失败，请检查文件')
    } finally {
      setBusy(false)
    }
  }

  const patchCol = (idx: number, patch: Partial<ParseFieldDef>) => {
    setCols((arr) => arr.map((c, i) => (i === idx ? { ...c, ...patch } : c)))
  }

  const removeCol = (idx: number) => {
    setCols((arr) => arr.filter((_, i) => i !== idx))
  }

  const save = async () => {
    if (!files.length || !parse || busy) return
    if (!name.trim()) {
      setErr('请填写模板名称')
      return
    }
    if (
      parse.tabs?.some(
        (tab) => !cols.some((c) => tab.field_keys.includes(c.key)),
      )
    ) {
      setErr('每个文件至少保留一个字段')
      return
    }
    setBusy(true)
    setErr('')
    try {
      const created = await createFromExcels(files, name.trim(), {
        ...parse,
        columns: cols,
      })
      setTemplateId(created.template_id)
      setBusy(false)
      setStep(4)
      toast(`模板「${name.trim()}」已创建，可继续派发`)
    } catch {
      setBusy(false) // 失败提示由拦截器统一弹
    }
  }

  const openHistory = async () => {
    setHistoryOpen(true)
    setHistoryBusy(true)
    try {
      setTemplates(
        (await listTemplates()).filter((item) => {
          const structure = item.parse_result
          return (
            item.status === 'active' &&
            !!item.source_file_name &&
            structure.columns.length > 0 &&
            (structure.tabs?.length ?? 0) > 0
          )
        }),
      )
    } finally {
      setHistoryBusy(false)
    }
  }

  const useTemplate = (template: DynamicTemplateItem) => {
    const structure = template.parse_result ?? {
      columns: [],
      tabs: template.tabs ?? [],
    }
    setTemplateId(template.template_id)
    setName(template.name)
    setParse(structure)
    setCols(structure.columns.map((column) => ({ ...column })))
    setActiveTab(structure.tabs?.[0]?.id ?? '')
    setHistoryOpen(false)
    setUsingHistory(true)
    setStep(4)
  }

  const goBack = () => {
    setErr('')
    if (step === 4 && usingHistory) {
      setHistoryOpen(true)
      return
    }
    setStep((current) => (current - 1) as 1 | 2 | 3)
  }

  const dispatch = async () => {
    if (!templateId || !assignees.length) {
      setErr('请选择处理范围，确保已解析到具体处理人')
      return
    }
    setBusy(true)
    try {
      const companyTarget = targets.find((target) => target.type === 'company')
      await api('POST', '/tasks', {
        template_key: `dyn_${templateId}`,
        assignee_ids: assignees,
        period: period || null,
        anchor: companyTarget ? { company: companyTarget.e.label } : null,
        due: due || null,
        reuse_mode: 'fresh',
        prev_task_id: null,
      })
      toast(`已派发给 ${assignees.length} 名处理人`)
      useTodoStore.getState().setBox('dispatcher')
      await useTodoStore.getState().load()
      // 后台刷新作为兜底；接口响应完成后再拉一次，覆盖可能早于提交的 SSE 查询。
      window.setTimeout(() => void useTodoStore.getState().load(), 250)
      onClose()
    } catch {
      setBusy(false)
    }
  }

  return (
    <ScmsModal title="上传 Excel 创建填报模板" onClose={onClose} closable={!busy}>
      <div className="tcc dispatch">
        {/* 步骤条 */}
        <div
          className="flex items-center gap-1 mb-3 text-xs"
          style={{ color: 'var(--ink-60)' }}
        >
          {STEPS.map((s, i) => (
            <span key={s} className="flex items-center gap-1">
              <span
                className="inline-flex items-center justify-center rounded-full"
                style={{
                  width: 18,
                  height: 18,
                  fontSize: 11,
                  background: step > i ? 'var(--acc, #1668dc)' : 'transparent',
                  border: `1px solid ${step > i ? 'var(--acc, #1668dc)' : 'var(--card-border)'}`,
                  color: step > i ? '#fff' : 'inherit',
                }}
              >
                {i + 1}
              </span>
              <span
                style={{
                  color:
                    step === i + 1 ? 'var(--ink-90, currentColor)' : 'inherit',
                }}
              >
                {s}
              </span>
              {i < STEPS.length - 1 && <span style={{ opacity: 0.4 }}>·</span>}
            </span>
          ))}
        </div>

        {step === 1 && (
          <>
            <div className="tcc-note-txt">
              支持同时选择多个 Excel，每个文件生成一个
              Tab。自动识别各文件首个工作表的标题、表头和字段类型，复杂宽表保留横向布局。
            </div>
            <input
              ref={fileRef}
              type="file"
              multiple
              accept=".xls,.xlsx,.xlsm"
              style={{ display: 'none' }}
              onChange={(e) => {
                const picked = Array.from(e.target.files ?? [])
                if (picked.length) void onPick(picked)
                e.target.value = ''
              }}
            />
            <button
              className="btn-ghost w-full"
              style={{ marginTop: 10, padding: '18px 0' }}
              disabled={busy}
              onClick={() => fileRef.current?.click()}
            >
              {busy
                ? '正在解析所有文件…'
                : '选择 Excel 文件（最多 10 个，单个 10MB，合计 30MB）'}
            </button>
            <button
              type="button"
              className="mt-3! flex w-full items-center justify-center gap-2 rounded-xl border border-sky-400 bg-sky-600 px-4! py-3.5! text-base font-semibold text-white shadow-sm transition-colors hover:bg-sky-700 disabled:opacity-50"
              disabled={busy}
              onClick={() => void openHistory()}
            >
              <span className="text-lg leading-none">▦</span> 选择历史模板
            </button>
          </>
        )}

        {step === 2 && parse && (
          <>
            <TemplateTabs
              tabs={parse.tabs ?? []}
              active={activeTab}
              onChange={setActiveTab}
            />
            <div className="tcc-note-txt">
              已解析 {files.length} 个文件、共 {cols.length} 个字段。切换 Tab
              分别调整名称 / 类型 / 必填，每页至少保留一个字段。
            </div>
            <div className="mt-2 overflow-x-auto">
              <table
                className="w-full text-xs"
                style={{ borderCollapse: 'collapse' }}
              >
                <thead>
                  <tr style={{ color: 'var(--ink-60)' }}>
                    <th className="text-left px-1 py-1.5 font-normal">
                      字段名称
                    </th>
                    <th className="text-left px-1 py-1.5 font-normal">类型</th>
                    <th className="px-1 py-1.5 font-normal">必填</th>
                    <th className="text-left px-1 py-1.5 font-normal">
                      选项（逗号分隔）
                    </th>
                    <th className="px-1 py-1.5" />
                  </tr>
                </thead>
                <tbody>
                  {currentColumns.map(({ c, i }) => (
                    <tr
                      key={`${c.key}-${i}`}
                      style={{ borderTop: '1px solid var(--card-border)' }}
                    >
                      <td className="px-1 py-1.5">
                        <input
                          className={inputCls}
                          style={{ ...inputStyle, minWidth: 110 }}
                          value={c.label}
                          onChange={(e) =>
                            patchCol(i, { label: e.target.value })
                          }
                        />
                      </td>
                      <td className="px-1 py-1.5">
                        <Select
                          size="small"
                          className="w-full"
                          style={{ minWidth: 90 }}
                          value={c.type}
                          onChange={(v) =>
                            patchCol(i, { type: v as ParseFieldDef['type'] })
                          }
                          options={FIELD_TYPES.map((t) => ({
                            value: t,
                            label: FIELD_TYPE_ZH[t],
                          }))}
                        />
                      </td>
                      <td className="px-1 py-1.5 text-center">
                        <input
                          type="checkbox"
                          checked={c.required}
                          onChange={(e) =>
                            patchCol(i, { required: e.target.checked })
                          }
                        />
                      </td>
                      <td className="px-1 py-1.5">
                        {c.type === 'select' ? (
                          <input
                            className={inputCls}
                            style={inputStyle}
                            placeholder="如：是,否"
                            value={(c.options ?? []).join(',')}
                            onChange={(e) =>
                              patchCol(i, {
                                options: e.target.value
                                  .split(/[,，]/)
                                  .map((s) => s.trim())
                                  .filter(Boolean),
                              })
                            }
                          />
                        ) : (
                          <span
                            style={{ color: 'var(--ink-40, var(--ink-60))' }}
                          >
                            —
                          </span>
                        )}
                      </td>
                      <td className="px-1 py-1.5 text-center">
                        <button
                          className="link-btn"
                          title="删除该字段"
                          onClick={() => removeCol(i)}
                          style={{ color: '#c25454' }}
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {sampleRows.length > 0 && (
              <div className="mt-2 overflow-x-auto" style={{ maxHeight: 120 }}>
                <table
                  className="w-full text-xs"
                  style={{ borderCollapse: 'collapse', color: 'var(--ink-60)' }}
                >
                  <thead>
                    <tr>
                      {currentColumns.map(({ c }) => (
                        <th
                          key={c.key}
                          className="text-left px-1 py-1 font-normal whitespace-nowrap"
                        >
                          {c.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sampleRows.map((row, i) => (
                      <tr key={i}>
                        {currentColumns.map(({ c }) => (
                          <td
                            key={c.key}
                            className="px-1 py-1 whitespace-nowrap"
                          >
                            {String(row[c.key] ?? '')}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        {step === 3 && (
          <>
            <div className="tcc-field">
              <label>
                模板名称<i className="tcc-req">*</i>
              </label>
              <input
                className="fill-input"
                placeholder="如：2026 年三季度经营数据收集"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="tcc-field">
              <label>字段预览（{cols.length} 个）</label>
              <div className="tcc-note-txt">
                {parse?.tabs?.map((tab) => (
                  <div key={tab.id} className="mb-2">
                    <strong>{tab.name}</strong>：
                    {cols
                      .filter((c) => tab.field_keys.includes(c.key))
                      .map((c) => c.label)
                      .join('、')}
                  </div>
                ))}
              </div>
            </div>
            <div className="tcc-note-txt" style={{ marginTop: 4 }}>
              保存后模板立即启用（系统级共享），派发弹窗任务类型下拉可选，填报人按表单填写后提交审核。
            </div>
          </>
        )}

        {step === 4 && (
          <>
            <div className="mb-4! rounded-xl border border-sky-400/20 bg-sky-500/8 p-4! text-base text-[var(--ink-70)]">
              模板已生成，现在可以直接选择办理企业并派发填报任务。
            </div>
            <div className="tcc-field">
              <label>
                处理范围<i className="tcc-req">*</i>
              </label>
              <div className="relative">
                <input
                  className="fill-input"
                  value={targetQuery}
                  onChange={(e) => {
                    setTargetQuery(e.target.value)
                    setTargetOpen(true)
                  }}
                  onFocus={() => setTargetOpen(true)}
                  placeholder="选择组织、具体用户、角色或分组"
                />
                {targetOpen && (
                  <div
                    className="mention-panel"
                    style={{ top: '100%', bottom: 'auto', left: 0, right: 0 }}
                  >
                    <DirectoryPicker
                      hideGroup
                      onClose={() => setTargetOpen(false)}
                      selectedTargetKeys={new Set(targets.map((item) => `${item.type}:${item.e.id}`))}
                      q={targetQuery}
                      onPick={(target) => {
                        setTargets((items) => {
                          const selected = items.some((item) => item.type === target.type && item.e.id === target.e.id)
                          return selected ? items.filter((item) => item.type !== target.type || item.e.id !== target.e.id) : [...items, target]
                        })
                        setTargetQuery('')
                        if (target.type !== 'user' && target.type !== 'role') setTargetOpen(false)
                      }}
                      maxHeight={220}
                    />
                  </div>
                )}
              </div>
            </div>
            {targets.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {targets.map((target, index) => (
                  <span
                    key={`${target.type}:${target.e.id}`}
                    className="mention-chip inline-flex items-center gap-1.5"
                  >
                    <span className="mention-tag">{target.groupZh}</span>
                    {target.e.label}
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() =>
                        setTargets((items) =>
                          items.filter((_, itemIndex) => itemIndex !== index),
                        )
                      }
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="tcc-field">
              <label>实际处理人</label>
              <div className="tcc-note-txt">
                {assignees.length
                  ? `${assigneeLabels.join('、')}（去重后 ${assignees.length} 人）`
                  : '选择范围后自动解析具体处理人'}
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="tcc-field">
                <label>填报期间</label>
                <input
                  className="fill-input"
                  value={period}
                  onChange={(e) => setPeriod(e.target.value)}
                  placeholder="2026-09"
                />
              </div>
              <div className="tcc-field">
                <label>截止时间</label>
                <input
                  type="date"
                  className="fill-input"
                  value={due}
                  onChange={(e) => setDue(e.target.value)}
                />
              </div>
            </div>
          </>
        )}

        {err && <div className="tcc-err">{err}</div>}

        <div className="rp-btns">
          {step > 1 && (
            <button className="btn-ghost" disabled={busy} onClick={goBack}>
              ← 上一步
            </button>
          )}
          {step === 2 && (
            <button
              className="btn-primary"
              disabled={
                busy ||
                cols.length === 0 ||
                parse?.tabs?.some(
                  (tab) => !cols.some((c) => tab.field_keys.includes(c.key)),
                )
              }
              onClick={() => {
                setErr('')
                setStep(3)
              }}
            >
              下一步 · 命名
            </button>
          )}
          {step === 3 && (
            <button
              className="btn-primary"
              disabled={busy}
              onClick={() => void save()}
            >
              {busy ? '保存中…' : '创建模板'}
            </button>
          )}
          {step === 4 && (
            <button
              className="btn-primary"
              disabled={busy || !assignees.length}
              onClick={() => void dispatch()}
            >
              {busy ? '派发中…' : '确认派发'}
            </button>
          )}
          {step === 1 && (
            <button className="btn-ghost" onClick={onClose}>
              取消
            </button>
          )}
        </div>
      </div>
      {historyOpen && (
        <ScmsModal
          title="历史填报模板"
          onClose={() => setHistoryOpen(false)}
          centered
          width="min(1120px, 96vw)"
          bodyMaxHeight="72vh"
          light
        >
          <div className="space-y-4 bg-white text-slate-700">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">
                  选择已有模板
                </h3>
                <p className="mt-1! text-base text-slate-500">
                  查看历史上传的模板结构，选择后直接进入任务派发。
                </p>
              </div>
              <button
                type="button"
                className="rounded-xl border border-slate-300 bg-white px-4! py-2! text-base font-semibold text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
                onClick={() => setHistoryOpen(false)}
              >
                ← 返回上传模板
              </button>
            </div>
            {historyBusy ? (
              <div className="py-12! text-center text-base text-slate-500">
                正在加载历史模板…
              </div>
            ) : templates.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-slate-300 py-12! text-center text-base text-slate-500">
                暂无可用的历史模板
              </div>
            ) : (
              <div className="grid gap-4 md:grid-cols-2">
                {templates.map((template) => {
                  const structure = template.parse_result ?? {
                    columns: [],
                    tabs: template.tabs ?? [],
                  }
                  return (
                    <article
                      key={template.template_id}
                      className="rounded-2xl border border-slate-200 bg-slate-50 p-5! shadow-sm"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h4 className="truncate font-semibold text-slate-900">
                            {template.name}
                          </h4>
                          <p className="mt-1! text-xs text-slate-500">
                            创建于 {fmtTemplateTime(template.created_at)}
                          </p>
                        </div>
                        <span className="shrink-0 rounded-full bg-emerald-100 px-2.5! py-1! text-xs font-medium text-emerald-700">
                          已启用
                        </span>
                      </div>
                      <div className="mt-4! space-y-2">
                        {(structure.tabs ?? []).map((tab) => (
                          <div
                            key={tab.id}
                            className="rounded-xl border border-slate-200 bg-white px-3! py-2.5!"
                          >
                            <div className="text-base font-medium text-slate-800">
                              {tab.name}
                            </div>
                            <div className="mt-1! line-clamp-2 text-xs leading-5 text-slate-500">
                              {structure.columns
                                .filter((field) =>
                                  tab.field_keys.includes(field.key),
                                )
                                .map((field) => field.label)
                                .join('、') || '暂无字段'}
                            </div>
                          </div>
                        ))}
                      </div>
                      <button
                        type="button"
                        className="mt-4! flex w-full items-center justify-center gap-2 rounded-xl border border-sky-600 bg-sky-600 px-4! py-3! text-base font-semibold text-white shadow-sm transition-colors hover:bg-sky-700"
                        onClick={() => useTemplate(template)}
                      >
                        使用此模板派发 <span aria-hidden="true">→</span>
                      </button>
                    </article>
                  )
                })}
              </div>
            )}
          </div>
        </ScmsModal>
      )}
    </ScmsModal>
  )
}

function fmtTemplateTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}
