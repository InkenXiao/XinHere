// 驾驶舱添加卡片弹窗：卡片存于服务端配置库（分组/类型/链接/技能），保存后全端生效
import { useEffect, useState } from 'react'
import Modal from '@/primitives/Modal'
import { api } from '@/transport/api'
import type { CockpitCardItem, XuanPuSkill } from '@/types'

type Kind = 'link' | 'task' | 'placeholder'

const KIND_ZH: Record<Kind, string> = { link: '链接', task: '技能任务', placeholder: '占位' }

export default function CockpitCardModal({
  groups,
  defaultGroup,
  onClose,
  onSaved,
}: {
  groups: { key: string; name: string }[]
  defaultGroup: string
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState('')
  const [groupKey, setGroupKey] = useState(defaultGroup || groups[0]?.key || '')
  const [kind, setKind] = useState<Kind>('link')
  const [subtitle, setSubtitle] = useState('')
  const [linkUrl, setLinkUrl] = useState('')
  const [skills, setSkills] = useState<XuanPuSkill[]>([])
  const [skillId, setSkillId] = useState<number | ''>('')
  const [sort, setSort] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    // 技能列表用于「技能任务」类卡片下拉（平台不可达时仍可添加链接/占位卡片）
    api<{ items: XuanPuSkill[] | { raw?: string } }>('GET', '/xuanpu/skills')
      .then((r) => setSkills(Array.isArray(r.items) ? (r.items as XuanPuSkill[]).filter((s) => s.is_active !== false) : []))
      .catch(() => {})
  }, [])

  const save = async () => {
    if (busy) return
    setErr('')
    const skill = kind === 'task' ? skills.find((s) => s.id === skillId) : null
    if (!name.trim()) return setErr('请填写卡片名称')
    if (!groupKey) return setErr('请选择所属分组')
    if (kind === 'link' && !linkUrl.trim()) return setErr('请填写链接地址')
    if (kind === 'task' && !skill) return setErr('请选择技能')
    setBusy(true)
    try {
      await api<CockpitCardItem>('POST', '/cockpit/cards', {
        key: `custom-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e6).toString(36)}`,
        group_key: groupKey,
        name: name.trim(),
        subtitle: subtitle.trim(),
        kind,
        link_url: kind === 'link' ? linkUrl.trim() : null,
        skill_id: skill?.id ?? null,
        skill_name: skill?.name ?? null,
        sort: sort.trim() ? Number(sort) || 0 : 0,
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
        <label>
          卡片名称<span className="req">*</span>
        </label>
        <input type="text" value={name} placeholder="如：行研入口" onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="form-row">
        <label>
          所属分组<span className="req">*</span>
        </label>
        <select value={groupKey} onChange={(e) => setGroupKey(e.target.value)}>
          {groups.map((g) => (
            <option key={g.key} value={g.key}>
              {g.name}
            </option>
          ))}
        </select>
      </div>
      <div className="form-row">
        <label>卡片类型</label>
        <div className="seg">
          {(Object.keys(KIND_ZH) as Kind[]).map((k) => (
            <button type="button" key={k} className={`seg-btn ${kind === k ? 'on' : ''}`} onClick={() => setKind(k)}>
              {KIND_ZH[k]}
            </button>
          ))}
        </div>
      </div>
      {kind === 'link' ? (
        <div className="form-row">
          <label>
            链接地址<span className="req">*</span>
          </label>
          <input type="text" value={linkUrl} placeholder="https://… 或 /pro/" onChange={(e) => setLinkUrl(e.target.value)} />
        </div>
      ) : null}
      {kind === 'task' ? (
        <div className="form-row">
          <label>
            选择技能<span className="req">*</span>
          </label>
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
      ) : null}
      <div className="form-row">
        <label>副标题</label>
        <input type="text" value={subtitle} placeholder="一行功能描述（可选）" onChange={(e) => setSubtitle(e.target.value)} />
      </div>
      <div className="form-row">
        <label>排序</label>
        <input type="number" value={sort} placeholder="数字越小越靠前（可选）" onChange={(e) => setSort(e.target.value)} />
      </div>
      {err && (
        <div className="modal-note" style={{ color: '#ef4444' }}>
          {err}
        </div>
      )}
    </Modal>
  )
}
