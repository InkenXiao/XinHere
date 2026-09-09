// Xin语页自定义卡片弹窗：添加「链接」或「技能」卡片（存于数据库，按用户维度）
import { useEffect, useState } from 'react'
import Modal from '@/primitives/Modal'
import { api } from '@/transport/api'
import type { HeroCardItem, XuanPuSkill } from '@/types'

export default function HeroCardModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'link' | 'skill'>('link')
  const [linkUrl, setLinkUrl] = useState('')
  const [skills, setSkills] = useState<XuanPuSkill[]>([])
  const [skillId, setSkillId] = useState<number | ''>('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    // 技能列表用于「技能」类卡片下拉（平台不可达时仍可添加链接卡片）
    api<{ items: XuanPuSkill[] | { raw?: string } }>('GET', '/xuanpu/skills')
      .then((r) => setSkills(Array.isArray(r.items) ? (r.items as XuanPuSkill[]).filter((s) => s.is_active !== false) : []))
      .catch(() => {})
  }, [])

  const save = async () => {
    if (busy) return
    setErr('')
    const skill = kind === 'skill' ? skills.find((s) => s.id === skillId) : null
    if (!name.trim()) return setErr('请填写卡片名称')
    if (kind === 'link' && !linkUrl.trim()) return setErr('请填写链接地址')
    if (kind === 'skill' && !skill) return setErr('请选择技能')
    setBusy(true)
    try {
      await api<HeroCardItem>('POST', '/hero/cards', {
        name: name.trim(),
        kind,
        link_url: kind === 'link' ? linkUrl.trim() : null,
        skill_id: skill?.id ?? null,
        skill_name: skill?.name ?? null,
      })
      onSaved()
      onClose()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="添加卡片"
      onClose={onClose}
      footer={
        <>
          <button className="btn-ghost" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button className="btn-primary" disabled={busy} onClick={() => void save()}>
            {busy ? '保存中…' : '保存'}
          </button>
        </>
      }
    >
      <div className="form-row">
        <label>卡片名称</label>
        <input type="text" value={name} placeholder="如：行研入口" onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="form-row">
        <label>卡片类型</label>
        <div className="seg">
          <button type="button" className={`seg-btn ${kind === 'link' ? 'on' : ''}`} onClick={() => setKind('link')}>
            链接
          </button>
          <button type="button" className={`seg-btn ${kind === 'skill' ? 'on' : ''}`} onClick={() => setKind('skill')}>
            技能
          </button>
        </div>
      </div>
      {kind === 'link' ? (
        <div className="form-row">
          <label>链接地址</label>
          <input
            type="text"
            value={linkUrl}
            placeholder="https://…"
            onChange={(e) => setLinkUrl(e.target.value)}
          />
        </div>
      ) : (
        <div className="form-row">
          <label>选择技能</label>
          <select value={skillId} onChange={(e) => setSkillId(e.target.value ? Number(e.target.value) : '')}>
            <option value="">请选择技能</option>
            {skills.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          {skills.length === 0 && <div className="form-hint">暂无可用技能，可先添加链接卡片</div>}
        </div>
      )}
      {err && (
        <div className="modal-note" style={{ color: '#ef4444' }}>
          {err}
        </div>
      )}
    </Modal>
  )
}
