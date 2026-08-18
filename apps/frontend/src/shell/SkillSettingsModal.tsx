// 技能设置弹窗：技能目录 + 启用开关（PUT /skills/{key}）
import { useEffect, useState } from 'react'
import Modal from '@/primitives/Modal'
import { api } from '@/transport/api'
import type { SkillItem } from '@/types'
import { useUiStore } from '@/state/uiStore'

export default function SkillSettingsModal() {
  const open = useUiStore((s) => s.skillSettingsOpen)
  if (!open) return null
  return <SettingsBody />
}

function SettingsBody() {
  const setOpen = useUiStore((s) => s.setSkillSettingsOpen)
  const toast = useUiStore((s) => s.toast)
  const [items, setItems] = useState<SkillItem[] | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    api<{ items: SkillItem[] }>('GET', '/skills')
      .then((r) => {
        if (live) setItems(r.items)
      })
      .catch((e) => {
        if (live) setErr((e as Error).message)
      })
    return () => {
      live = false
    }
  }, [])

  const toggle = async (s: SkillItem) => {
    const next = !s.enabled
    setItems((prev) => prev?.map((x) => (x.skill_key === s.skill_key ? { ...x, enabled: next } : x)) ?? null)
    try {
      await api('PUT', `/skills/${s.skill_key}`, { enabled: next })
      toast(next ? `已启用「${s.name}」` : `已停用「${s.name}」`)
    } catch (e) {
      setItems((prev) => prev?.map((x) => (x.skill_key === s.skill_key ? { ...x, enabled: !next } : x)) ?? null)
      toast(`设置失败：${(e as Error).message}`, 'err')
    }
  }

  return (
    <Modal title="技能设置" onClose={() => setOpen(false)}>
      <div className="modal-note">
        选择 AI 问数可使用的技能；停用的技能不会在对话中触发对应工具。
      </div>
      {err && <div className="err-card">加载失败：{err}</div>}
      {!err && !items && <div className="modal-note">加载中…</div>}
      {items && (
        <div className="skill-list">
          {items.map((s) => (
            <div className="skill-row" key={s.skill_key}>
              <div className="sr-main">
                <div className="sr-name">
                  {s.name}
                  {s.core && <span className="sr-badge">核心</span>}
                  {s.file_type && <span className="sr-badge">.{s.file_type}</span>}
                </div>
                <div className="sr-desc">{s.desc}</div>
              </div>
              <button
                className={`switch ${s.enabled ? 'on' : ''}`}
                onClick={() => void toggle(s)}
                role="switch"
                aria-checked={s.enabled}
                aria-label={`${s.name} 启用开关`}
              >
                <i />
              </button>
            </div>
          ))}
        </div>
      )}
    </Modal>
  )
}
