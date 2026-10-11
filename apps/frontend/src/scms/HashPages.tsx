// scms 移植三页的 hash 宿主：#/oa、#/repo-alerts、#/config/cockpit
// 命中时整屏接管（替代主界面），antd 前缀隔离 + 弹层挂 #scms-portal；返回清 hash 回主界面。
import { useEffect, useState } from 'react'
import ScmsProvider from './ScmsProvider'
import OaPage from './pages/OaPage'
import RepoAlertsPage from './pages/RepoAlertsPage'
import CockpitConfigPage from './pages/CockpitConfigPage'

export type HashPageKey = 'oa' | 'repo-alerts' | 'config-cockpit'

function parseHash(): HashPageKey | null {
  const h = window.location.hash.replace(/^#\/?/, '')
  if (h === 'oa') return 'oa'
  if (h === 'repo-alerts') return 'repo-alerts'
  if (h === 'config/cockpit') return 'config-cockpit'
  return null
}

export function useHashPage(): HashPageKey | null {
  const [key, setKey] = useState<HashPageKey | null>(() => parseHash())
  useEffect(() => {
    const on = () => setKey(parseHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return key
}

export default function HashPages({ page }: { page: HashPageKey }) {
  return (
    <div className="scms-root" style={{ position: 'fixed', inset: 0, zIndex: 2000, overflow: 'auto', background: 'var(--bg-1, #10151f)' }}>
      <div id="scms-portal" />
      <ScmsProvider>
        {page === 'oa' && <OaPage />}
        {page === 'repo-alerts' && <RepoAlertsPage />}
        {page === 'config-cockpit' && <CockpitConfigPage />}
      </ScmsProvider>
    </div>
  )
}
