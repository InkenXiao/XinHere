// 录音小窗入口：渲染 RecorderApp；autostart 握手（防双开 + 防刷新静默重录）
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import RecorderApp from './RecorderApp'
import { connectId, useMeetingStore } from '@/state/meetingStore'
import '../styles/theme.css'

const params = new URLSearchParams(location.search)
const autostart = params.get('autostart') === '1'
// 刷新防护：上一会话标记存在说明是刷新而非新开（旧录音已随上下文销毁）
const wasActive = sessionStorage.getItem('xinhere.recorder.active') === '1'

// StrictMode 会双执行 effect，用模块级标记守卫自启逻辑
let bootStarted = false
function boot() {
  if (bootStarted) return
  bootStarted = true
  if (!autostart || wasActive) {
    if (wasActive) {
      sessionStorage.removeItem('xinhere.recorder.active')
      useMeetingStore.setState({ statusText: '录音已中断，转写内容已丢失' })
    }
    return
  }
  // 广播 hello 等 400ms：发现其它小窗已在录音则不自动开录（防双开互踩）
  useMeetingStore.getState().hello()
  let otherRecording = false
  const ch = new BroadcastChannel('xinhere.meeting')
  ch.onmessage = (e) => {
    const m = e.data
    if (m?.type === 'sync' && m.from !== connectId && m.payload?.recording) {
      otherRecording = true
    }
  }
  setTimeout(() => {
    ch.close()
    if (!otherRecording) void useMeetingStore.getState().start()
  }, 400)
}

function Root() {
  boot()
  return (
    <StrictMode>
      <RecorderApp />
    </StrictMode>
  )
}

createRoot(document.getElementById('root')!).render(<Root />)
