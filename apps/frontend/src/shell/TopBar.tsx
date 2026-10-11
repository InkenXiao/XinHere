// 顶栏：品牌 logo（随模式切换）/ 连接态 / 平台消息 / 用户菜单
import { useEffect, useRef, useState } from 'react'
import { useAuthStore } from '@/state/authStore'
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'
import ModelPrefsModal from './ModelPrefsModal'
import ChangePasswordModal, { scmsAuthAvailable } from './ChangePasswordModal'
import XuanpuMsgBell from './XuanpuMsgBell'

const ROLE_ZH: Record<string, string> = { hq_finance: '本部财务', investee_finance: '被投财务' }

export default function TopBar() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const connStatus = useSessionStore((s) => s.connStatus)
  const mode = useUiStore((s) => s.mode)
  const cockpitEditing = useUiStore((s) => s.cockpitEditing)
  const setCockpitEditing = useUiStore((s) => s.setCockpitEditing)
  const [menuOpen, setMenuOpen] = useState(false)
  const [pwdOpen, setPwdOpen] = useState(false)
  const [canChangePwd, setCanChangePwd] = useState(false)
  const [prefsOpen, setPrefsOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // 点击外部关闭用户菜单
  useEffect(() => {
    void scmsAuthAvailable().then(setCanChangePwd)
  }, [])

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
        <img className="brand-logo cockpit" src="/assets/logo-cockpit.svg" alt="XinHere" />
        <img className="brand-logo tower" src="/assets/logo-watchtower.svg" alt="XinHere" />
        <span className="slogan">  ·  </span>
      </div>
      <span />
      <div className="topbar-right">
        <span className={`conn-dot ${connStatus === 'reconnecting' ? 'reconnecting' : ''}`}>
          <i />
          {connStatus === 'reconnecting' ? '重连中' : '已连接'}
        </span>
        <XuanpuMsgBell />
        <div className="user-menu" ref={menuRef}>
          <button className="user-chip" onClick={() => setMenuOpen((v) => !v)}>
            {user?.display_name ?? '…'}
            <span className="role-tag">{ROLE_ZH[user?.role ?? ''] ?? ''}</span>
            {user?.company ?? ''}
          </button>
          {menuOpen && (
            <div className="user-pop">
              {mode === 'cockpit' && (user?.permissions ?? []).includes('xinhere.cockpit.manage') && (
                <div className="pop-switch">
                  <span>页面编辑</span>
                  <button
                    type="button"
                    className={`sw${cockpitEditing ? ' on' : ''}`}
                    role="switch"
                    aria-checked={cockpitEditing}
                    aria-label="页面编辑"
                    onClick={() => setCockpitEditing(!cockpitEditing)}
                  >
                    <i />
                  </button>
                </div>
              )}
              <button
                onClick={() => {
                  setMenuOpen(false)
                  setPrefsOpen(true)
                }}
              >
                个人设置
              </button>
              {canChangePwd && (
                <button
                  onClick={() => {
                    setMenuOpen(false)
                    setPwdOpen(true)
                  }}
                >
                  修改密码
                </button>
              )}
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
      <ModelPrefsModal open={prefsOpen} onClose={() => setPrefsOpen(false)} />
      <ChangePasswordModal open={pwdOpen} onClose={() => setPwdOpen(false)} />
    </header>
  )
}
