// 录音小窗 UI：红点呼吸 + 时长 + 波形 + 停止/开始 + 打开会议页
// 本窗口即 controller（真实录音源），状态全部来自 useMeetingStore
import { useMeetingStore } from '@/state/meetingStore'

function fmt(sec: number) {
  const m = Math.floor(sec / 60)
  const s = Math.max(0, Math.floor(sec % 60))
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export default function RecorderApp() {
  const recording = useMeetingStore((s) => s.recording)
  const finalizing = useMeetingStore((s) => s.finalizing)
  const elapsed = useMeetingStore((s) => s.elapsed)
  const error = useMeetingStore((s) => s.error)
  const transcriptError = useMeetingStore((s) => s.transcriptError)
  const audioUrl = useMeetingStore((s) => s.audioUrl)
  const start = useMeetingStore((s) => s.start)
  const stop = useMeetingStore((s) => s.stop)
  const openMeetingPage = useMeetingStore((s) => s.openMeetingPage)

  const err = error || transcriptError
  const stopped = !recording && !finalizing

  return (
    <div className="rc-app">
      <button className="rc-main" onClick={() => openMeetingPage()} title="打开会议页">
        <span className="rc-top">
          <span className={`rc-dot${recording ? ' on' : ''}`} />
          <span className="rc-state">
            {recording ? '录音中' : finalizing ? '正在结束…' : audioUrl ? '录音已停止' : '准备就绪'}
          </span>
          {recording && <span className="rc-time">{fmt(elapsed)}</span>}
        </span>
        {recording && (
          <span className="rc-wave" aria-hidden="true">
            {Array.from({ length: 14 }).map((_, i) => (
              <span key={i} style={{ animationDelay: `${(i % 7) * 0.13}s` }} />
            ))}
          </span>
        )}
        <span className="rc-hint">
          {err ? (
            <span className="rc-err">{err}</span>
          ) : recording ? (
            '请保持本窗口开启，录音将持续进行'
          ) : audioUrl ? (
            '录音已生成，可在下方回放'
          ) : (
            '点击此处打开会议页'
          )}
        </span>
      </button>

      {stopped && audioUrl && <audio className="rc-audio" controls src={audioUrl} />}

      <div className="rc-foot">
        {stopped ? (
          <button className="rc-btn rc-btn--start" onClick={() => void start()}>
            开始录音
          </button>
        ) : (
          <button className="rc-btn rc-btn--stop" disabled={finalizing} onClick={() => void stop()}>
            {finalizing ? '正在结束…' : '停止录音'}
          </button>
        )}
        <button className="rc-btn rc-btn--ghost" onClick={() => openMeetingPage()}>
          打开会议页
        </button>
      </div>
    </div>
  )
}
