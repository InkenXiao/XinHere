// scms 三页的极简 hash 导航：#/oa、#/repo-alerts、#/config/cockpit；goBack 清 hash 回主界面
export function goBack(): void {
  try {
    window.location.hash = ''
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
  } catch { /* ignore */ }
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}
