// 待办「去填报/去审批」弹窗宿主：按 scene 分派到四个填报视图；其余场景引导回对话
import { useEffect, useState } from 'react'
import { useUiStore, type SceneTarget } from '@/state/uiStore'
import { useAuthStore } from '@/state/authStore'
import { useTodoStore } from '@/state/todoStore'
import { api } from '@/transport/api'
import type { CashReport } from '@/types'
import Modal from '@/primitives/Modal'
import { RiskFillView } from '@/plugins/RiskFillForm'
import { CashGuaranteeView } from '@/plugins/CashGuaranteeForm'
import { KpiFillView } from '@/plugins/KpiFillForm'
import { MsFeedbackView } from '@/plugins/MsFeedbackForm'

export default function SceneModal() {
  const target = useUiStore((s) => s.sceneTarget)
  if (!target) return null
  return <SceneBody key={`${target.scene}:${target.title}`} target={target} />
}

function SceneBody({ target }: { target: SceneTarget }) {
  const openScene = useUiStore((s) => s.openScene)
  const toast = useUiStore((s) => s.toast)
  const user = useAuthStore((s) => s.user)
  const [cash, setCash] = useState<CashReport | null>(null)
  const [cashErr, setCashErr] = useState<string | null>(null)

  useEffect(() => {
    if (target.scene !== 'cash_guarantee') return
    let live = true
    api<CashReport>('GET', `/cash-guarantees/${String(target.ref.form_id ?? '')}`)
      .then((r) => {
        if (live) setCash(r)
      })
      .catch((e) => {
        if (live) setCashErr((e as Error).message)
      })
    return () => {
      live = false
    }
  }, [target])

  const done = () => {
    openScene(null)
    void useTodoStore.getState().load()
    toast('已完成')
  }
  const company = String(target.ref.company ?? user?.company ?? '')

  const body = () => {
    switch (target.scene) {
      case 'risk_fill':
        return (
          <RiskFillView
            batchId={String(target.ref.batch_id ?? '')}
            company={company}
            mode={target.mode ?? 'fill'}
            onDone={done}
          />
        )
      case 'cash_guarantee': {
        if (cashErr) return <div className="err-card">加载失败：{cashErr}</div>
        if (!cash) return <div className="modal-note">加载中…</div>
        return (
          <CashGuaranteeView
            company={cash.company}
            period={cash.period}
            initial={{
              avail_cash: cash.avail_cash,
              pooled_fund: cash.pooled_fund,
              avail_credit: cash.avail_credit,
              monthly_outflow: cash.monthly_outflow,
            }}
            onUpdate={async (v) => {
              await api('PUT', `/cash-guarantees/${cash.form_id}`, v)
              toast('草稿已保存')
            }}
            onSubmit={async (v) => {
              await api('PUT', `/cash-guarantees/${cash.form_id}`, v)
              await api('POST', `/cash-guarantees/${cash.form_id}/submit`)
              done()
            }}
            onCancel={async () => openScene(null)}
          />
        )
      }
      case 'kpi_fill':
        return <KpiFillView batchId={String(target.ref.batch_id ?? '')} company={company} onDone={done} />
      case 'ms_feedback':
        return (
          <MsFeedbackView feedbackId={String(target.ref.feedback_id ?? '')} fallback={{ company }} onDone={done} />
        )
      default:
        // lamp_adjust / report / generic：面板内引导回对话
        return (
          <div>
            <div className="modal-note">该事项请在对话中处理。</div>
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button
                className="btn-primary"
                onClick={() => {
                  const todoId = target.ref.todo_id as string | undefined
                  if (todoId) void useTodoStore.getState().complete(todoId)
                  done()
                }}
              >
                标记完成
              </button>
            </div>
          </div>
        )
    }
  }

  return (
    <Modal title={target.title} onClose={() => openScene(null)}>
      {body()}
    </Modal>
  )
}
