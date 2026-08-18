// 顶栏：brand+slogan / 子系统外链 / 连接态 / 用户菜单
import { useEffect, useRef, useState } from 'react'
import { runtimeEnv } from '@/config'
import { useAuthStore } from '@/state/authStore'
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'

const ROLE_ZH: Record<string, string> = { hq_finance: '本部财务', investee_finance: '被投财务' }

// 子系统跳转（运行时配置 window.__ENV__，空值不渲染；名称对齐设计稿）
const SUB_SYSTEMS = [
  { name: '业务系统', url: runtimeEnv.OPS_URL || undefined, ext: false },
  { name: '青山知识库', url: runtimeEnv.KB_URL || undefined, ext: false },
  { name: 'cowork', url: runtimeEnv.COWORK_URL || undefined, ext: true },
].filter((s) => s.url)

export default function TopBar() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const connStatus = useSessionStore((s) => s.connStatus)
  const setActiveScreen = useUiStore((s) => s.setActiveScreen)
  const theme = useUiStore((s) => s.theme)
  const toggleTheme = useUiStore((s) => s.toggleTheme)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // 主题同步到 <html data-theme>
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    document.documentElement.style.colorScheme = theme
  }, [theme])

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
            {s.ext && <span className="ext-arrow">↗</span>}
          </a>
        ))}
      </nav>
      <div className="topbar-right">
        <button
          className="theme-btn"
          onClick={toggleTheme}
          title={theme === 'dark' ? '切换白天模式' : '切换深色模式'}
          aria-label="主题切换"
        >
          {theme === 'dark' ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="12" cy="12" r="4" fill="currentColor" stroke="none" />
              <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="currentColor">
              <path d="M21.75 15.5A9.72 9.72 0 0 1 12.3 21a9.75 9.75 0 0 1-9.75-9.75A9.72 9.72 0 0 1 8.05 1.7a.75.75 0 0 1 .96.96 8.25 8.25 0 0 0 11.88 11.88.75.75 0 0 1 .86.96z" />
            </svg>
          )}
        </button>
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
                  useUiStore.getState().setSkillSettingsOpen(true)
                }}
              >
                技能设置
              </button>
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
