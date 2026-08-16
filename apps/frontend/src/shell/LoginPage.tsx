// 登录页：演示账号 hq01 / Xin@2026（mock 模式口令任意非空）
import { useState } from 'react'
import { useAuthStore } from '@/state/authStore'

export default function LoginPage() {
  const login = useAuthStore((s) => s.login)
  const [username, setUsername] = useState('hq01')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (busy) return
    if (!username.trim() || !password) {
      setErr('请输入用户名和密码')
      return
    }
    setBusy(true)
    setErr(null)
    try {
      await login(username.trim(), password)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-stage">
      <form
        className="login-card"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <div className="login-brand">
          <span className="mark" />
          <h1>XinHere</h1>
          <span className="slogan">新在这里，心在这里</span>
        </div>
        <div className="login-field">
          <label>用户名</label>
          <input value={username} autoFocus onChange={(e) => setUsername(e.target.value)} placeholder="hq01" />
        </div>
        <div className="login-field">
          <label>密码</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Xin@2026" />
        </div>
        {err && <div className="login-err">{err}</div>}
        <button className="btn-primary" style={{ width: '100%', padding: '11px 0' }} disabled={busy} type="submit">
          {busy ? '登录中…' : '登 录'}
        </button>
        <div className="login-hint">演示账号 hq01 · 口令 Xin@2026（被投财务 inv01~inv11；mock 模式口令任意非空）</div>
      </form>
    </div>
  )
}
