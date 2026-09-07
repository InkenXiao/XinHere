// XuanPu 填报弹窗：拉模板字段 + 已存草稿预填（分节/必填/帮助文案）；存草稿或提交
import { useEffect, useState } from 'react'
import { api } from '@/transport/api'
import Modal from '@/primitives/Modal'
import { useTodoStore } from '@/state/todoStore'
import { useUiStore } from '@/state/uiStore'
import type { XuanPuFillAssignmentDetail, XuanPuFillField, XuanPuFillTemplateDetail } from '@/types'

type FillValue = string | string[]

export default function FillFormModal() {
  const target = useUiStore((s) => s.fillTarget)
  const openFill = useUiStore((s) => s.openFill)
  const toast = useUiStore((s) => s.toast)
  const [tpl, setTpl] = useState<XuanPuFillTemplateDetail | null>(null)
  const [asg, setAsg] = useState<XuanPuFillAssignmentDetail | null>(null)
  const [data, setData] = useState<Record<string, FillValue>>({})
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const assignmentId = target?.assignmentId
  const templateId = target?.templateId

  useEffect(() => {
    if (assignmentId == null || templateId == null) return
    setTpl(null)
    setAsg(null)
    setData({})
    setErr('')
    let alive = true
    Promise.all([
      api<XuanPuFillTemplateDetail>('GET', `/xuanpu/fill/template?template_id=${templateId}`),
      api<XuanPuFillAssignmentDetail>('GET', `/xuanpu/fill/assignment?assignment_id=${assignmentId}`),
    ])
      .then(([t, a]) => {
        if (!alive) return
        if (t.raw) {
          setErr(t.raw)
          return
        }
        setTpl(t)
        setAsg(a)
        const d: Record<string, FillValue> = {}
        for (const f of t.fields ?? []) {
          const v = a.draft?.[f.key]
          d[f.key] = f.type === 'multi' ? (Array.isArray(v) ? (v as string[]) : []) : v == null ? '' : String(v)
        }
        setData(d)
      })
      .catch((e: unknown) => {
        if (alive) setErr(e instanceof Error ? e.message : String(e))
      })
    return () => {
      alive = false
    }
  }, [assignmentId, templateId])

  const save = async (mode: 'draft' | 'submit') => {
    if (assignmentId == null) return
    if (mode === 'submit') {
      for (const f of tpl?.fields ?? []) {
        if (!f.required) continue
        const v = data[f.key]
        const empty = Array.isArray(v) ? v.length === 0 : !String(v ?? '').trim()
        if (empty) {
          toast(`请填写「${f.label}」`, 'err')
          return
        }
      }
    }
    setBusy(true)
    try {
      const r = await api<{ raw?: string }>('POST', `/xuanpu/fill/${mode}`, {
        assignment_id: assignmentId,
        data,
      })
      if (r.raw) {
        toast(r.raw, 'err')
        return
      }
      toast(mode === 'draft' ? '草稿已保存' : '填报已提交')
      await useTodoStore.getState().load()
      openFill(null)
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    } finally {
      setBusy(false)
    }
  }

  if (assignmentId == null || templateId == null) return null

  // 按 section 分节（无 section 归入空节）
  const sections: { name: string; fields: XuanPuFillField[] }[] = []
  for (const f of tpl?.fields ?? []) {
    const name = f.section || ''
    const last = sections[sections.length - 1]
    if (last && last.name === name) last.fields.push(f)
    else sections.push({ name, fields: [f] })
  }

  return (
    <Modal
      title={`填报 · ${asg?.title || tpl?.title || ''}`}
      onClose={() => openFill(null)}
      footer={
        <>
          <button className="btn-ghost" disabled={busy || !tpl} onClick={() => void save('draft')}>
            保存草稿
          </button>
          <button className="btn-primary" disabled={busy || !tpl} onClick={() => void save('submit')}>
            提交
          </button>
        </>
      }
    >
      {err ? (
        <div className="modal-note" style={{ color: '#ef4444' }}>
          {err}
        </div>
      ) : !tpl ? (
        <div className="modal-note">加载中…</div>
      ) : (
        <>
          {asg?.description && <div className="modal-note">{asg.description}</div>}
          {asg?.status === 'submitted' && (
            <div className="modal-note">
              已提交{asg.submitted_at ? ` · ${asg.submitted_at}` : ''}，修改后可重新提交
            </div>
          )}
          {sections.map((sec, i) => (
            <div key={i}>
              {sec.name && <div className="ff-sec">{sec.name}</div>}
              {sec.fields.map((f) => (
                <div className="form-row" key={f.key}>
                  <label>
                    {f.label}
                    {f.required && <span className="req">*</span>}
                  </label>
                  {f.type === 'multi' ? (
                    <div className="ff-multi">
                      {(f.options ?? []).map((o) => {
                        const checked = ((data[f.key] as string[]) ?? []).includes(o)
                        return (
                          <label key={o} className="ff-opt">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(e) => {
                                const cur = new Set((data[f.key] as string[]) ?? [])
                                if (e.target.checked) cur.add(o)
                                else cur.delete(o)
                                setData({ ...data, [f.key]: [...cur] })
                              }}
                            />
                            {o}
                          </label>
                        )
                      })}
                    </div>
                  ) : (
                    <input
                      className="fill-input"
                      type={f.type === 'number' ? 'number' : 'text'}
                      value={(data[f.key] as string) ?? ''}
                      placeholder={f.placeholder || ''}
                      onChange={(e) => setData({ ...data, [f.key]: e.target.value })}
                    />
                  )}
                  {f.help && <span className="ff-help">{f.help}</span>}
                </div>
              ))}
            </div>
          ))}
          {sections.length === 0 && <div className="modal-note">该填报暂无字段，可直接提交</div>}
        </>
      )}
    </Modal>
  )
}
