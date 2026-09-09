// 执行记录详情弹窗：状态 + 技能 + 输出摘要（Xin台 AI 能力执行记录）
import Modal from '@/primitives/Modal'
import { useUiStore } from '@/state/uiStore'
import { fmtTime } from '@/utils'

const STATUS_LABEL: Record<string, string> = {
  running: '运行中',
  success: '已完成',
  failed: '未完成',
  stopped: '已停止',
}

export default function TaskDetailModal() {
  const rec = useUiStore((s) => s.execDetail)
  const openExecDetail = useUiStore((s) => s.openExecDetail)
  if (!rec) return null

  const d = rec.detail ?? {}
  const output = typeof d.output === 'string' ? d.output : ''
  const error = typeof d.error === 'string' ? d.error : ''
  const skill = typeof d.skill === 'string' ? d.skill : ''

  return (
    <Modal
      title={`执行记录 · ${rec.title}`}
      onClose={() => openExecDetail(null)}
      footer={
        <button className="btn-ghost" onClick={() => openExecDetail(null)}>
          关闭
        </button>
      }
    >
      <div className="modal-note">
        {STATUS_LABEL[rec.status] ?? '执行记录'}
        {skill && ` · ${skill}`}
        {rec.updated_at && ` · ${fmtTime(rec.updated_at)}`}
      </div>
      {output && <pre className="sr-out">{output}</pre>}
      {error && (
        <div className="modal-note" style={{ color: '#ef4444' }}>
          {error}
        </div>
      )}
      {!output && !error && <div className="modal-note">暂无输出内容</div>}
    </Modal>
  )
}
