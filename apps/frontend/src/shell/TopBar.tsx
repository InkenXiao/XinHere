// 顶栏：品牌 logo（随模式切换）/ 连接态 / 用户菜单
import { useEffect, useRef, useState } from 'react'
import { useAuthStore } from '@/state/authStore'
import { useSessionStore } from '@/state/sessionStore'

const ROLE_ZH: Record<string, string> = { hq_finance: '本部财务', investee_finance: '被投财务' }

export default function TopBar() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const connStatus = useSessionStore((s) => s.connStatus)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // 点击外部关闭用户菜单
  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menuOpen])

  return (
    <header className="topbar">
      <div className="brand">
        <img className="brand-logo cockpit" src="/assets/logo-cockpit.png" alt="XinHere" />
        <img className="brand-logo tower" src="/assets/logo-tower.png" alt="XinHere" />
        <span className="slogan">新在这里，心在这里</span>
      </div>
      <span />
      <div className="topbar-right">
        <span className={`conn-dot ${connStatus === 'reconnecting' ? 'reconnecting' : ''}`}>
          <i />
          {connStatus === 'reconnecting' ? '重连中' : '已连接'}
        </span>
        <div className="user-menu" ref={menuRef}>
          <button className="user-chip" onClick={() => setMenuOpen((v) => !v)}>
            {user?.display_name ?? '…'}
            <span className="role-tag">{ROLE_ZH[user?.role ?? ''] ?? ''}</span>
            {user?.company ?? ''}
          </button>
          {menuOpen && (
            <div className="user-pop">
              <button
                onClick={() => {
                  setMenuOpen(false)
                  void logout()
                }}
              >
                退出登录
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
