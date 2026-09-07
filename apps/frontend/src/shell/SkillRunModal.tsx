// XuanPu 技能直跑弹窗：运行（同步等待终态）→ 展示输出或错误
import { useEffect, useState } from 'react'
import { api } from '@/transport/api'
import Modal from '@/primitives/Modal'
import { useUiStore } from '@/state/uiStore'
import type { XuanPuSkillRunResult } from '@/types'

export default function SkillRunModal() {
  const target = useUiStore((s) => s.runTarget)
  const openRun = useUiStore((s) => s.openRun)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<XuanPuSkillRunResult | null>(null)
  const [err, setErr] = useState('')

  const skillId = target?.skillId

  // 切换技能时清空上一轮结果
  useEffect(() => {
    if (skillId != null) {
      setResult(null)
      setErr('')
    }
  }, [skillId])

  const run = async () => {
    if (skillId == null || busy) return
    setBusy(true)
    setErr('')
    try {
      const r = await api<XuanPuSkillRunResult>('POST', `/xuanpu/skills/${skillId}/run`, { inputs: {} })
      if (r.raw) setErr(r.raw)
      else setResult(r)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (target == null) return null

  const out = result?.output_data
  const outText = out == null ? '' : typeof out === 'string' ? out : JSON.stringify(out, null, 2)

  return (
    <Modal
      title={`技能 · ${target.name}`}
      onClose={() => openRun(null)}
      footer={
        <>
          <button className="btn-ghost" disabled={busy} onClick={() => openRun(null)}>
            关闭
          </button>
          <button className="btn-primary" disabled={busy} onClick={() => void run()}>
            {busy ? '运行中…' : result ? '再次运行' : '运行'}
          </button>
        </>
      }
    >
      {target.desc && <div className="modal-note">{target.desc}</div>}
      {busy && <div className="modal-note">运行中，长耗时技能可能需要数十秒…</div>}
      {err && (
        <div className="modal-note" style={{ color: '#ef4444' }}>
          {err}
        </div>
      )}
      {result && result.status === 'success' && <pre className="sr-out">{outText || '运行完成'}</pre>}
      {result && result.status !== 'success' && (
        <div className="modal-note" style={{ color: '#ef4444' }}>
          运行未完成：{result.error || result.note || result.status}
        </div>
      )}
    </Modal>
  )
}
