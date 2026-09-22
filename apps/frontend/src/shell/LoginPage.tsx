// 登录页：统一账号登录为主（SSO 跳转）+ 本机账号兜底（默认收起；演示 hq01 / Xin@2026）
import { useState } from 'react'
import { useAuthStore } from '@/state/authStore'
import { API_BASE, mockEnabled } from '@/transport/api'

export default function LoginPage() {
  const login = useAuthStore((s) => s.login)
  const mock = mockEnabled()
  const [showLocal, setShowLocal] = useState(mock) // mock 演示模式无后端 SSO，默认展开本机表单
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
          <img className="logo" src="/assets/login.svg" alt="XinHere" />
          <h1>XinHere</h1>
          <span className="slogan">信在此 · 新在此</span>
        </div>
        {!mock && (
          <button
            type="button"
            className="btn-primary"
            style={{ width: '100%', padding: '11px 0' }}
            onClick={() => {
              window.location.href = `${API_BASE}/auth/sso/login`
            }}
          >
            使用统一账号登录
          </button>
        )}
        <button type="button" className="login-split" onClick={() => setShowLocal((v) => !v)}>
          <span className="line" />
          或使用本机账号
          <span className="line" />
        </button>
        {showLocal && (
          <>
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
          </>
        )}
      </form>
    </div>
  )
}
