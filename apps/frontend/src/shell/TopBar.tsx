// 顶栏：brand+slogan / 子系统外链 / 连接态 / 用户菜单
import { useEffect, useRef, useState } from 'react'
import { useAuthStore } from '@/state/authStore'
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'

const ROLE_ZH: Record<string, string> = { hq_finance: '本部财务', investee_finance: '被投财务' }

// 子系统跳转（.env VITE_* 配置，空值不渲染）
const SUB_SYSTEMS = [
  { name: '运营管理系统', url: import.meta.env.VITE_OPS_URL as string | undefined },
  { name: '青山知识库', url: import.meta.env.VITE_KB_URL as string | undefined },
  { name: 'CoWork', url: import.meta.env.VITE_COWORK_URL as string | undefined },
].filter((s) => s.url)

export default function TopBar() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const connStatus = useSessionStore((s) => s.connStatus)
  const setActiveScreen = useUiStore((s) => s.setActiveScreen)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // 两屏 active 跟踪：按 .screens 滚动位置（写入 uiStore 供屏1隐藏历史/待办栏）
  useEffect(() => {
    const scroller = document.querySelector('.screens')
    if (!scroller) return
    const onScroll = () =>
      setActiveScreen(scroller.scrollTop >= window.innerHeight * 0.5 ? 'dash' : 'work')
    scroller.addEventListener('scroll', onScroll, { passive: true })
    return () => scroller.removeEventListener('scroll', onScroll)
  }, [setActiveScreen])

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
        <span className="mark" />
        XinHere
        <span className="slogan">新在这里，心在这里</span>
      </div>
      <nav className="nav">
        {SUB_SYSTEMS.map((s) => (
          <a key={s.name} href={s.url} target="_blank" rel="noreferrer">
            {s.name}
          </a>
        ))}
      </nav>
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
