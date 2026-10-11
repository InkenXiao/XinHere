// 改密弹窗（SCMS 账号体系接入后可用；未接入时菜单不显示）
import { useEffect, useState } from 'react'
import { api, ApiError } from '@/transport/api'

export function scmsAuthAvailable(): Promise<boolean> {
  return api<{ items: { key: string }[] }>('GET', '/config/navs')
    .then((r) => r.items.some((n) => n.key === 'scms'))
    .catch(() => false)
}

export default function ChangePasswordModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!open) {
      setOldPwd(''); setNewPwd(''); setConfirmPwd(''); setMsg(null)
    }
  }, [open])

  if (!open) return null

  const submit = async () => {
    setMsg(null)
    if (newPwd !== confirmPwd) {
      setMsg({ ok: false, text: '两次输入的新密码不一致' })
      return
    }
    setBusy(true)
    try {
      await api('POST', '/auth/change-password', {
        old_password: oldPwd, new_password: newPwd,
      })
      setMsg({ ok: true, text: '密码已更新，下次登录请使用新密码' })
      setOldPwd(''); setNewPwd(''); setConfirmPwd('')
    } catch (e) {
      const text = e instanceof ApiError ? e.message : '修改失败，请稍后再试'
      setMsg({ ok: false, text })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal change-pwd" onClick={(e) => e.stopPropagation()}>
        <div className="m-head">
          <span>修改密码</span>
          <button className="m-close" onClick={onClose}>×</button>
        </div>
        <div className="m-body">
          <label>当前密码
            <input type="password" value={oldPwd} onChange={(e) => setOldPwd(e.target.value)}
              placeholder="请输入当前密码" autoComplete="current-password" />
          </label>
          <label>新密码
            <input type="password" value={newPwd} onChange={(e) => setNewPwd(e.target.value)}
              placeholder="8-16位，含字母、数字和特殊字符" autoComplete="new-password" />
          </label>
          <label>确认新密码
            <input type="password" value={confirmPwd} onChange={(e) => setConfirmPwd(e.target.value)}
              placeholder="再次输入新密码" autoComplete="new-password" />
          </label>
          {msg && <div className={`form-msg ${msg.ok ? 'ok' : 'err'}`}>{msg.text}</div>}
        </div>
        <div className="m-foot">
          <button className="ghost" onClick={onClose}>取消</button>
          <button className="primary" disabled={busy || !oldPwd || !newPwd} onClick={() => void submit()}>
            {busy ? '提交中…' : '确认修改'}
          </button>
        </div>
      </div>
    </div>
  )
}
