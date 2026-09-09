// 实时会议页：录音控制 + 增量转写 + 阶段/最终纪要 + 润色/拷贝/保存
// 关闭页面仅收起 UI，录音由 meetingStore 持有后台继续
import { useEffect, useRef } from 'react'
import { useUiStore } from '@/state/uiStore'
import { useMeetingStore } from '@/state/meetingStore'

function fmtDur(sec: number) {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function fmtLine(sec: number) {
  const m = Math.floor(sec / 60)
  const s = Math.max(0, Math.floor(sec % 60))
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export default function MeetingPage() {
  const open = useUiStore((s) => s.meetingOpen)
  const setMeetingOpen = useUiStore((s) => s.setMeetingOpen)
  const recording = useMeetingStore((s) => s.recording)
  const finalizing = useMeetingStore((s) => s.finalizing)
  const elapsed = useMeetingStore((s) => s.elapsed)
  const transcript = useMeetingStore((s) => s.transcript)
  const transcriptError = useMeetingStore((s) => s.transcriptError)
  const minutesRaw = useMeetingStore((s) => s.minutesRaw)
  const minutesMode = useMeetingStore((s) => s.minutesMode)
  const summarizing = useMeetingStore((s) => s.summarizing)
  const saving = useMeetingStore((s) => s.saving)
  const statusText = useMeetingStore((s) => s.statusText)
  const audioUrl = useMeetingStore((s) => s.audioUrl)
  const error = useMeetingStore((s) => s.error)

  const transRef = useRef<HTMLDivElement>(null)
  const minutesRef = useRef<HTMLPreElement>(null)

  // 转写/纪要更新自动贴底
  useEffect(() => {
    const el = transRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [transcript])
  useEffect(() => {
    const el = minutesRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [minutesRaw])

  if (!open) return null

  const m = useMeetingStore.getState()
  const hasMinutes = minutesRaw.trim().length > 0

  return (
    <section className="meeting-page" aria-hidden={!open}>
      <div className="mt-panel mt-panel--wide">
        <header className="mt-head">
          <h2>实时会议</h2>
          <button className="btn-ghost" onClick={() => setMeetingOpen(false)}>
            {recording ? '收起（录音继续）' : '收起'}
          </button>
        </header>

        <div className={`mt-stage ${recording ? 'on' : ''}`}>
          <div className="mt-wave" aria-hidden="true">
            {Array.from({ length: 24 }).map((_, i) => (
              <span key={i} className="bar" style={{ animationDelay: `${(i % 8) * 0.12}s` }} />
            ))}
          </div>
          <div className="mt-time">
            {recording ? fmtDur(elapsed) : finalizing ? '正在结束…' : hasMinutes ? '录音已停止' : '未开始'}
          </div>
          <div className="mt-hint">
            {recording
              ? '正在录音，语音实时转写；每分钟自动生成阶段纪要，收起页面后录音继续'
              : finalizing
                ? '正在结清转写并生成最终纪要…'
                : '点击开始录音，转写与会议纪要实时生成'}
          </div>
          {error && <div className="mt-err">{error}</div>}
        </div>

        <div className="mt-body">
          <section className="mt-col">
            <div className="mt-col-head">
              <span>实时转写</span>
              <span className="mt-col-meta">{transcript.length} 句</span>
            </div>
            <div className="mt-transcript" ref={transRef}>
              {transcript.length === 0 && !recording && (
                <div className="mt-empty">开始录音后，语音将实时转写为文字</div>
              )}
              {transcript.map((l, i) => (
                <div className="mt-line" key={i}>
                  <span className="mt-line-ts">[{fmtLine(l.sec)}]</span>
                  {l.text}
                </div>
              ))}
              {recording && transcript.length > 0 && <div className="mt-line mt-line--live">…</div>}
            </div>
            {transcriptError && <div className="mt-col-err">{transcriptError}</div>}
          </section>

          <section className="mt-col">
            <div className="mt-col-head">
              <span>会议纪要</span>
              <span className="mt-col-meta">{summarizing ? '生成中…' : hasMinutes ? `${minutesRaw.length} 字` : '—'}</span>
            </div>
            {minutesMode === 'edit' ? (
              <textarea
                className="mt-minutes-edit"
                value={minutesRaw}
                onChange={(e) => m.setMinutesRaw(e.target.value)}
                placeholder="编辑纪要内容，完成后点「完成编辑」"
              />
            ) : (
              <pre className="mt-minutes" ref={minutesRef}>
                {hasMinutes ? minutesRaw : <span className="mt-empty">转写开始后可生成纪要；录音中每分钟自动生成阶段纪要</span>}
              </pre>
            )}
          </section>
        </div>

        <div className="mt-ops">
          {minutesMode === 'edit' ? (
            <button className="btn-primary" onClick={() => m.setMinutesMode('view')}>
              ✔ 完成编辑
            </button>
          ) : (
            <>
              <button className="btn-ghost" disabled={!transcript.length || summarizing} onClick={() => m.generateFinal()}>
                📝 生成纪要
              </button>
              <button className="btn-ghost" disabled={!hasMinutes || summarizing} onClick={() => m.polish()}>
                ✨ 润色
              </button>
              <button className="btn-ghost" disabled={!hasMinutes} onClick={() => void m.copyMinutes()}>
                📋 拷贝
              </button>
              <button className="btn-ghost" disabled={!hasMinutes || saving} onClick={() => void m.saveMeeting()}>
                {saving ? '保存中…' : '💾 保存会议纪要'}
              </button>
              {hasMinutes && minutesMode === 'view' && (
                <button className="btn-ghost" onClick={() => m.setMinutesMode('edit')}>
                  ✏️ 编辑
                </button>
              )}
            </>
          )}
          {statusText && <span className="mt-status">{statusText}</span>}
        </div>

        <footer className="mt-foot">
          {recording ? (
            <button className="btn-primary stop" disabled={finalizing} onClick={() => void m.stop()}>
              停止录音
            </button>
          ) : (
            <button className="btn-primary" disabled={finalizing} onClick={() => void m.start()}>
              {finalizing ? '正在结束…' : '开始录音'}
            </button>
          )}
          {audioUrl && (
            <div className="mt-play">
              <audio controls src={audioUrl} />
            </div>
          )}
        </footer>
      </div>
    </section>
  )
}
