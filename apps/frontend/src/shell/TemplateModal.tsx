// 技能模版选择弹窗：列出技能模版卡片，点击 → 话术进入 AI 问数对话
import { useEffect, useState } from 'react'
import Modal from '@/primitives/Modal'
import { api } from '@/transport/api'
import type { SkillTemplate } from '@/types'
import { useUiStore } from '@/state/uiStore'
import { useSessionStore } from '@/state/sessionStore'

const SKILL_NAMES: Record<string, string> = {
  post_report: '投后管理报告',
  fin_risk_report: '财务风险报告',
  info_fill: '信息填报',
  kpi_fill: '经营者考核',
  ms_feedback: '里程碑反馈',
  lamp_adjust: '亮灯调整',
  task_stats: '任务执行统计',
  generic_dispatch: '通用派发',
}

export default function TemplateModal() {
  const skillKey = useUiStore((s) => s.templateSkill)
  if (!skillKey) return null
  return <TemplateBody key={skillKey} skillKey={skillKey} />
}

function TemplateBody({ skillKey }: { skillKey: string }) {
  const close = useUiStore((s) => s.closeTemplateModal)
  const toast = useUiStore((s) => s.toast)
  const [items, setItems] = useState<SkillTemplate[] | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    api<{ items: SkillTemplate[] }>('GET', `/skills/${skillKey}/templates`)
      .then((r) => {
        if (live) setItems(r.items)
      })
      .catch((e) => {
        if (live) setErr((e as Error).message)
      })
    return () => {
      live = false
    }
  }, [skillKey])

  const pick = async (t: SkillTemplate) => {
    if (t.content.status === 'dev') {
      toast('开发中，敬请期待')
      return
    }
    const prompt = t.content.prompt || t.name
    close()
    await useSessionStore.getState().newSession()
    useUiStore.getState().setWorkView('chat')
    void useSessionStore.getState().send(prompt)
  }

  return (
    <Modal title={`${SKILL_NAMES[skillKey] ?? skillKey} · 选择模版`} onClose={close}>
      {err && <div className="err-card">加载失败：{err}</div>}
      {!err && !items && <div className="modal-note">加载中…</div>}
      {items && items.length === 0 && (
        <div className="modal-note" style={{ textAlign: 'center', padding: '28px 0' }}>
          🚧 开发中，敬请期待
        </div>
      )}
      {items && items.length > 0 && (
        <div className="tpl-grid">
          {items.map((t) => {
            const dev = t.content.status === 'dev' || !t.enabled
            return (
              <button
                key={t.template_id}
                className={`tpl-card ${dev ? 'dev' : ''}`}
                onClick={() => void pick(t)}
              >
                <span className="tp-name">
                  {t.name}
                  {t.category && <span className="tp-cat">{t.category}</span>}
                  {t.content.file_type && <span className="tp-cat">.{t.content.file_type}</span>}
                </span>
                {dev && <span className="tp-dev">开发中，敬请期待</span>}
              </button>
            )
          })}
        </div>
      )}
    </Modal>
  )
}
