// 实时会议 · 分段录音 + 增量转写 + 阶段/最终纪要 + 润色 + 保存
// 双角色架构（防刷新中断）：
//   controller（录音小窗 recorder.html）：真实持有麦克风，唯一数据源，广播状态
//   mirror（主页面）：镜像状态 + 转发操作；小窗断连后纯 API 操作本地降级
// 通信：BroadcastChannel('xinhere.meeting')，消息 { type, from, payload }
//   tick（高频小载荷，300ms 尾随节流）/ sync（低频内容增量，脏检查）
//   hello（请求快照）/ cmd（操作转发）/ died（小窗卸载即时通知）
import { create } from 'zustand'
import { api, apiUpload } from '@/transport/api'
import { streamMinutes } from '@/transport/sse'
import { loadAudio, saveAudio, deleteAudio } from '@/transport/audioCache'
import type { MeetingLine } from '@/types'
import { useUiStore } from './uiStore'

const SEGMENT_MS = 2000 // 分段长度：延时 ≈ 段长 + 转写耗时 ≈ 2~3s
const SUMMARY_MS = 60000 // 每 60s 自动生成一次阶段纪要（且有 ≥30 字新增转写时）
const RECORDER_FEATURES = 'width=360,height=190,popup=yes,menubar=no,toolbar=no,location=no,status=no,resizable=yes'
const HELLO_TIMEOUT_MS = 1500 // 主页面握手超时：期间无小窗回应则视为无录音在途
const HEARTBEAT_TIMEOUT_MS = 8000 // 心跳超时兜底（小窗进程崩溃场景；正常关窗有 died 即时通知）
const SESSION_ACTIVE_KEY = 'xinhere.recorder.active'

// ---------- 角色判定（window.name 由 window.open 第二参数写入；query 兜底，子目录/Hash 路由安全） ----------
const IS_RECORDER =
  typeof window !== 'undefined' &&
  (window.name === 'XinHereRecorder' || new URLSearchParams(window.location.search).get('recorder') === '1')

// ---------- 跨窗口通信 ----------
const CHANNEL = 'xinhere.meeting'
let channel: BroadcastChannel | null = null
function chan(): BroadcastChannel {
  if (!channel) channel = new BroadcastChannel(CHANNEL)
  return channel
}

interface ChanMsg {
  type: 'tick' | 'sync' | 'hello' | 'cmd' | 'died' | 'openMeeting' | 'meetingAck'
  from: string
  payload?: any
}

function send(type: ChanMsg['type'], payload?: any) {
  chan().postMessage({ type, from: MY_ID, payload } satisfies ChanMsg)
}

/** 本窗口随机 id（来源锁定 + 自消息过滤） */
const MY_ID = Math.random().toString(36).slice(2, 10)

interface MeetingState {
  connecting: boolean // 主页面握手过渡态：防止刷新瞬间按钮状态闪烁误触
  recording: boolean // 录音中（controller 真录音；mirror 镜像自小窗）
  finalizing: boolean // 正在结束录音（结末段/合成回放/生成最终纪要）
  startedAt: number | null
  elapsed: number // 已录时长（秒）
  transcript: MeetingLine[] // 已按序落定的转写行
  transcriptError: string | null
  minutesRaw: string // 纪要原文（markdown）
  minutesMode: 'view' | 'edit'
  summarizing: boolean
  saving: boolean
  statusText: string
  audioUrl: string | null // 本地 blob URL（两窗口各自从同一 audioId 的 IndexedDB Blob 生成）
  audioId: string | null // 音频 IndexedDB 主键：跨窗口共享回放
  error: string | null
  hello: () => void // 广播快照请求（小窗回应 sync+tick；主页面据此恢复状态）
  openMeetingPage: () => void // 小窗唤起主页面会议页（600ms 无回应新开主页面）
  start: () => Promise<void>
  stop: () => Promise<void>
  reset: () => void
  generateFinal: () => void
  polish: () => void
  copyMinutes: () => Promise<void>
  setMinutesMode: (m: 'view' | 'edit') => void
  setMinutesRaw: (t: string) => void
  saveMeeting: () => Promise<void>
}

export const useMeetingStore = create<MeetingState>((set, get, store) => {
  const notify = (t: string) => set({ statusText: t })

  // ==================== 录音单例（仅 controller 真正使用） ====================
  let stream: MediaStream | null = null
  let tickTimer: ReturnType<typeof setInterval> | null = null
  let fileRecorder: MediaRecorder | null = null // 长录音器：专供回放音频
  let fileChunks: Blob[] = []
  let segRecorder: MediaRecorder | null = null // 分段转写录音器（每段新建）
  let segTimer: ReturnType<typeof setTimeout> | null = null
  let segSeq = 0 // 已开出的段序号
  let segBuffer = new Map<number, MeetingLine[]>() // 段序号 → 转写行（乱序缓冲）
  let segFlushed = 0 // 已按序落定的最大连续段序号
  let segStopFlag = false
  let segResolve: (() => void) | null = null
  let segStartMs = 0 // 当前段起始时间戳
  let lastSummarizeBucket = -1
  let lastSummarizeTextLength = 0 // 阶段纪要防刷：上次总结时的转写字数
  let runId = 0 // 纪要流自增序号，丢弃过期流
  let minutesBuf = '' // 纪要增量缓冲
  let minutesFlushTimer: ReturnType<typeof setTimeout> | null = null

  // ==================== 广播（controller 侧） ====================
  // tick：高频小载荷，300ms 尾随节流；elapsed 每秒递增天然构成心跳
  let tickPending = false
  const broadcastTick = () => {
    if (tickPending) return
    tickPending = true
    setTimeout(() => {
      tickPending = false
      const s = get()
      send('tick', {
        recording: s.recording,
        finalizing: s.finalizing,
        elapsed: s.elapsed,
        summarizing: s.summarizing,
        saving: s.saving,
        statusText: s.statusText,
      })
    }, 300)
  }

  // sync：低频内容增量，脏检查（transcript 只发新增行；minutesRaw 值变才全量）
  let lastSentTranscriptCount = 0
  let lastSentMinutes = ''
  let lastSentError: string | null | undefined
  let lastSentTranscriptError: string | null | undefined
  let lastSentAudioId: string | null | undefined
  const broadcastSync = (full = false) => {
    const s = get()
    const append = full ? s.transcript : s.transcript.slice(lastSentTranscriptCount)
    const payload: any = {}
    if (full) {
      payload.recording = s.recording
      payload.finalizing = s.finalizing
      payload.elapsed = s.elapsed
      payload.summarizing = s.summarizing
      payload.saving = s.saving
      payload.statusText = s.statusText
      payload.transcript = s.transcript
      payload.minutesRaw = s.minutesRaw
      payload.minutesMode = s.minutesMode
      payload.error = s.error
      payload.transcriptError = s.transcriptError
      payload.audioId = s.audioId
    } else {
      if (append.length > 0) payload.transcriptAppend = append
      if (s.minutesRaw !== lastSentMinutes) payload.minutesRaw = s.minutesRaw
      if (s.error !== lastSentError) payload.error = s.error
      if (s.transcriptError !== lastSentTranscriptError) payload.transcriptError = s.transcriptError
      if (s.audioId !== lastSentAudioId) payload.audioId = s.audioId
    }
    lastSentTranscriptCount = s.transcript.length
    lastSentMinutes = s.minutesRaw
    lastSentError = s.error
    lastSentTranscriptError = s.transcriptError
    lastSentAudioId = s.audioId
    if (Object.keys(payload).length > 0 || full) send('sync', payload)
  }

  /** zustand subscribe 处理器：controller 状态变化 → 双通道差异化广播 */
  const onStateChange = (s: MeetingState, prev: MeetingState) => {
    if (!IS_RECORDER) return
    const tickTouched =
      s.recording !== prev.recording ||
      s.finalizing !== prev.finalizing ||
      s.elapsed !== prev.elapsed ||
      s.summarizing !== prev.summarizing ||
      s.saving !== prev.saving ||
      s.statusText !== prev.statusText
    if (tickTouched) broadcastTick()
    const syncTouched =
      s.transcript !== prev.transcript ||
      s.minutesRaw !== prev.minutesRaw ||
      s.error !== prev.error ||
      s.transcriptError !== prev.transcriptError ||
      s.audioId !== prev.audioId
    if (syncTouched) broadcastSync()
  }

  // ==================== 镜像（mirror 侧） ====================
  let lockedFrom: string | null = null // 来源锁定：仅接受首个快照源
  let lastMsgAt = 0 // 距最后一条来自锁定源的消息时间（存活判定）
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let recorderWin: Window | null = null // 主页面持有的小窗引用（focus 掀到前台）
  let helloTimeout: ReturnType<typeof setTimeout> | null = null

  const stopHeartbeat = () => {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  /** mirror 收到小窗快照：应用 + 来源锁定 + 心跳计时 */
  const applySnapshot = (from: string, p: any, full: boolean) => {
    if (from === MY_ID) return
    if (lockedFrom && lockedFrom !== from) return // 已锁定其它源，忽略（防多小窗互踩）
    const first = lockedFrom === null
    lockedFrom = from
    lastMsgAt = Date.now()
    stopHeartbeat()
    heartbeatTimer = setInterval(() => {
      // 心跳超时兜底：died 没送达（进程崩溃）时也要中断
      if (lockedFrom && Date.now() - lastMsgAt > HEARTBEAT_TIMEOUT_MS) {
        lockedFrom = null
        stopHeartbeat()
        if (get().recording) set({ recording: false, statusText: '录音已中断' })
      }
    }, 2000)
    if (helloTimeout) {
      clearTimeout(helloTimeout)
      helloTimeout = null
    }
    if (get().connecting) set({ connecting: false })
    const patch: Partial<MeetingState> = {}
    if (full || p.recording !== undefined) patch.recording = !!p.recording
    if (full || p.finalizing !== undefined) patch.finalizing = !!p.finalizing
    if (full || p.elapsed !== undefined) patch.elapsed = p.elapsed ?? 0
    if (full || p.summarizing !== undefined) patch.summarizing = !!p.summarizing
    if (full || p.saving !== undefined) patch.saving = !!p.saving
    if (full || p.statusText !== undefined) patch.statusText = p.statusText ?? ''
    if (full || p.minutesRaw !== undefined) patch.minutesRaw = p.minutesRaw ?? ''
    if (full || p.minutesMode !== undefined) patch.minutesMode = p.minutesMode ?? 'view'
    if (full || p.error !== undefined) patch.error = p.error ?? null
    if (full || p.transcriptError !== undefined) patch.transcriptError = p.transcriptError ?? null
    if (full || p.transcript !== undefined) patch.transcript = p.transcript ?? []
    if (full || p.transcriptAppend !== undefined) {
      const app: MeetingLine[] = p.transcriptAppend ?? []
      patch.transcript = first ? app : [...get().transcript, ...app]
    }
    // audioId：从 IndexedDB 读 Blob 生成本地回放地址（不依赖小窗的 blob URL）
    const newAudioId: string | null = full ? p.audioId ?? null : p.audioId !== undefined ? p.audioId : null
    if (newAudioId && newAudioId !== get().audioId) {
      patch.audioId = newAudioId
      void loadAudio(newAudioId).then((blob) => {
        if (!blob) return
        const prev = get().audioUrl
        const url = URL.createObjectURL(blob)
        set({ audioUrl: url })
        if (prev) URL.revokeObjectURL(prev)
      })
    } else if (full && !newAudioId && get().audioUrl) {
      const prev = get().audioUrl
      set({ audioUrl: null })
      if (prev) URL.revokeObjectURL(prev)
    }
    if (Object.keys(patch).length > 0) set(patch)
  }

  // ==================== 消息分发 ====================
  const onMessage = (e: MessageEvent) => {
    const m = e.data as ChanMsg
    if (!m || m.from === MY_ID) return
    if (IS_RECORDER) {
      // ---- controller 侧 ----
      if (m.type === 'hello') {
        broadcastSync(true) // 全量快照 + 基线重置
        broadcastTick()
      } else if (m.type === 'cmd') {
        const a = m.payload?.action
        if (a === 'stop') void get().stop()
        else if (a === 'generateFinal') get().generateFinal()
        else if (a === 'polish') get().polish()
        else if (a === 'saveMeeting') void get().saveMeeting()
        else if (a === 'setMinutesRaw') get().setMinutesRaw(m.payload?.value ?? '')
      } else if (m.type === 'meetingAck') {
        ackReceived = true
      }
      return
    }
    // ---- mirror 侧 ----
    if (m.type === 'tick' || m.type === 'sync') {
      applySnapshot(m.from, m.payload, m.type === 'sync' && m.payload?.recording !== undefined && m.payload?.transcript !== undefined)
      if (m.type === 'tick') {
        // tick 为高频心跳：仅更新高频字段与心跳时间
        const p = m.payload ?? {}
        const patch: Partial<MeetingState> = {}
        if (p.recording !== undefined && p.recording !== get().recording) patch.recording = !!p.recording
        if (p.finalizing !== undefined && p.finalizing !== get().finalizing) patch.finalizing = !!p.finalizing
        if (p.elapsed !== undefined && p.elapsed !== get().elapsed) patch.elapsed = p.elapsed
        if (p.summarizing !== undefined && p.summarizing !== get().summarizing) patch.summarizing = !!p.summarizing
        if (p.saving !== undefined && p.saving !== get().saving) patch.saving = !!p.saving
        if (p.statusText !== undefined && p.statusText !== get().statusText) patch.statusText = p.statusText
        if (Object.keys(patch).length > 0) set(patch)
      }
    } else if (m.type === 'died') {
      if (lockedFrom === m.from || lockedFrom === null) {
        lockedFrom = null
        stopHeartbeat()
        if (get().recording) set({ recording: false, statusText: '录音已中断' })
      }
    } else if (m.type === 'openMeeting') {
      useUiStore.getState().setMeetingOpen(true)
      window.focus()
      send('meetingAck')
    }
  }

  let ackReceived = false // 小窗「打开会议页」600ms 未收到 ack 则新开主页面
  /** 小窗主动唤起主页面会议页：广播 openMeeting，600ms 无回应则新开主页面 */
  const openMeetingPage = () => {
    ackReceived = false
    send('openMeeting')
    setTimeout(() => {
      if (!ackReceived) window.open(`${import.meta.env.BASE_URL}`, '_blank')
    }, 600)
  }

  // ==================== 启动装配 ====================
  chan().addEventListener('message', onMessage)
  // create 回调同步执行，此时 const useMeetingStore 尚未完成赋值，直接引用自身会抛 TDZ 错误；
  // zustand creator 第三参数即 store 实例，经它订阅
  store.subscribe(onStateChange)

  if (IS_RECORDER) {
    // 小窗：关闭/刷新前广播 died + 弹浏览器原生确认
    window.addEventListener('pagehide', () => {
      if (get().recording || get().finalizing) {
        try {
          chan().postMessage({ type: 'died', from: MY_ID, payload: {} } satisfies ChanMsg)
        } catch { /* 卸载期尽力投递 */ }
      }
    })
    window.addEventListener('beforeunload', (e) => {
      if (get().recording || get().finalizing) {
        e.preventDefault()
        e.returnValue = ''
      }
    })
  } else {
    // 主页面：加载即握手，短暂 connecting 防「未在录音→录音中」闪烁误触
    set({ connecting: true })
    send('hello')
    helloTimeout = setTimeout(() => set({ connecting: false }), HELLO_TIMEOUT_MS)
  }

  /** 打开录音小窗（必须在用户手势同步栈内调用 window.open） */
  const openRecorderWindow = (): Window | null => {
    const left = Math.max(0, (window.screen.availWidth ?? 1440) - 380)
    const top = Math.max(0, (window.screen.availHeight ?? 900) - 240)
    const url = `${import.meta.env.BASE_URL}recorder.html?recorder=1&autostart=1`
    return window.open(url, 'XinHereRecorder', `${RECORDER_FEATURES},left=${left},top=${top}`)
  }

  function fmtSec(sec: number) {
    const m = Math.floor(sec / 60)
    const s = Math.max(0, Math.floor(sec % 60))
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  }

  function transcriptText(lines: MeetingLine[]) {
    return lines.map((l) => `[${fmtSec(l.sec)}] ${l.text}`).join('\n')
  }

  /** 按序落定转写行（乱序缓冲，O(Δ) 渲染） */
  const flushBuffer = () => {
    const out = [...get().transcript]
    let changed = false
    while (segBuffer.has(segFlushed + 1)) {
      segFlushed += 1
      const lines = segBuffer.get(segFlushed) ?? []
      segBuffer.delete(segFlushed)
      out.push(...lines)
      changed = true
    }
    if (changed) set({ transcript: out })
  }

  /** 单段转写（异步，不阻塞采集；失败也推进段序防卡序） */
  const transcribeSegment = async (blob: Blob, seq: number, startSec: number) => {
    let lines: MeetingLine[] = []
    try {
      const type = blob.type || 'audio/webm'
      const ext = type.includes('ogg') ? 'ogg' : 'webm'
      const res = await apiUpload<{
        ok: boolean
        text?: string
        segments?: { start: number; text: string }[]
        error?: string
      }>('/audio/transcribe', blob, `seg.${ext}`)
      if (res.ok) {
        const segs = res.segments && res.segments.length > 0
          ? res.segments
          : res.text && res.text.trim()
            ? [{ start: 0, text: res.text }]
            : []
        for (const s of segs) {
          const t = (s.text || '').trim()
          if (t) lines.push({ sec: Math.round((s.start || 0) + startSec), text: t })
        }
      } else {
        set({ transcriptError: res.error || '语音转写失败' })
      }
    } catch (e) {
      set({ transcriptError: `转写失败：${e instanceof Error ? e.message : String(e)}` })
    }
    segBuffer.set(seq, lines)
    flushBuffer()
  }

  /** 开新分段（每段销毁重建 recorder，保证每段都是含完整头的独立音频文件） */
  const startSegment = () => {
    if (!stream || segStopFlag) return
    const rec = new MediaRecorder(stream)
    segRecorder = rec
    const seq = ++segSeq
    segStartMs = Date.now()
    let chunk: Blob | null = null
    rec.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunk = e.data
    }
    rec.onstop = () => {
      const blob = new Blob(chunk ? [chunk] : [], { type: rec.mimeType || 'audio/webm' })
      const startSec = Math.max(0, Math.round((segStartMs - (get().startedAt ?? segStartMs)) / 1000))
      if (blob.size > 800) void transcribeSegment(blob, seq, startSec)
      else segBuffer.set(seq, [])
      flushBuffer()
      if (!segStopFlag && stream) startSegment()
      else segResolve?.()
    }
    rec.start()
    segTimer = setTimeout(() => {
      if (rec.state === 'recording') rec.stop()
    }, SEGMENT_MS)
  }

  /** 纪要增量合并渲染（100ms 尾随） */
  const scheduleMinutesFlush = (expectedRun: number) => {
    if (minutesFlushTimer) return
    minutesFlushTimer = setTimeout(() => {
      minutesFlushTimer = null
      if (runId === expectedRun) set({ minutesRaw: minutesBuf })
    }, 100)
  }

  /** 纪要生成：stage 阶段 / final 最终 / polish 润色（润色以当前纪要为输入） */
  const summarize = async (mode: 'stage' | 'final' | 'polish') => {
    const source =
      mode === 'polish' ? get().minutesRaw : transcriptText(get().transcript)
    if (!source.trim()) {
      useUiStore.getState().toast('暂无可用于生成的内容', 'err')
      return
    }
    const myRun = ++runId
    minutesBuf = ''
    set({
      summarizing: true,
      minutesRaw: '',
      minutesMode: 'view',
      statusText: mode === 'polish' ? '润色中…' : mode === 'final' ? '纪要生成中…' : '阶段纪要生成中…',
    })
    try {
      await streamMinutes({ transcript: source, mode }, {
        onDelta: (d) => {
          if (runId !== myRun) return
          minutesBuf += d
          scheduleMinutesFlush(myRun)
        },
        onDone: () => {},
      })
    } catch (e) {
      if (runId === myRun) minutesBuf += `\n[纪要生成失败] ${e instanceof Error ? e.message : String(e)}`
    }
    if (runId !== myRun) return
    set({
      minutesRaw: minutesBuf,
      summarizing: false,
      statusText: minutesBuf.startsWith('\n[纪要生成失败]') ? '纪要生成失败' : `已生成 ${minutesBuf.length} 字`,
    })
  }

  /** mirror 判定小窗是否存活（距最后快照 < 5s） */
  const controllerAlive = () => lockedFrom !== null && Date.now() - lastMsgAt < 5000

  return {
    connecting: !IS_RECORDER, // 主页面初始 connecting，握手后解除
    recording: false,
    finalizing: false,
    startedAt: null,
    elapsed: 0,
    transcript: [],
    transcriptError: null,
    minutesRaw: '',
    minutesMode: 'view',
    summarizing: false,
    saving: false,
    statusText: '',
    audioUrl: null,
    audioId: null,
    error: null,

    hello() {
      send('hello')
    },

    openMeetingPage,

    async start() {
      if (IS_RECORDER) {
        // ---- controller：真实开录 ----
        if (get().recording || get().finalizing) return
        const prevUrl = get().audioUrl
        if (prevUrl) URL.revokeObjectURL(prevUrl)
        const prevAudioId = get().audioId
        if (prevAudioId) void deleteAudio(prevAudioId).catch(() => {}) // 新录音清理旧音频，防存储膨胀
        set({
          error: null,
          transcriptError: null,
          transcript: [],
          minutesRaw: '',
          minutesMode: 'view',
          elapsed: 0,
          startedAt: null,
          audioUrl: null,
          audioId: null,
          statusText: '正在连接麦克风…',
        })
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        } catch {
          set({ error: '无法访问麦克风，请检查浏览器授权', statusText: '' })
          useUiStore.getState().toast('无法访问麦克风，请检查浏览器授权', 'err')
          return
        }
        segSeq = 0
        segBuffer = new Map()
        segFlushed = 0
        segStopFlag = false
        lastSummarizeBucket = -1
        lastSummarizeTextLength = 0
        runId += 1
        minutesBuf = ''
        fileChunks = []
        // 长录音器（回放用，start(timeslice) 仅落缓冲、整体一个文件）
        fileRecorder = new MediaRecorder(stream)
        fileRecorder.ondataavailable = (e) => {
          if (e.data && e.data.size > 0) fileChunks.push(e.data)
        }
        fileRecorder.start(5000)
        set({ recording: true, startedAt: Date.now(), statusText: '录音进行中' })
        sessionStorage.setItem(SESSION_ACTIVE_KEY, '1') // 刷新防护标记
        startSegment()
        tickTimer = setInterval(() => {
          const st = get()
          if (!st.recording) return
          const elapsed = st.elapsed + 1
          set({ elapsed })
          // 60s 跨桶 + 新增转写 ≥30 字才触发阶段纪要（静音期不做无效调用）
          const bucket = Math.floor(elapsed / (SUMMARY_MS / 1000))
          if (bucket > lastSummarizeBucket) {
            lastSummarizeBucket = bucket
            const chars = transcriptText(st.transcript).length
            if (chars - lastSummarizeTextLength >= 30 && !st.summarizing) {
              lastSummarizeTextLength = chars
              void summarize('stage')
            }
          }
        }, 1000)
        return
      }
      // ---- mirror ----
      if (get().connecting) return // 握手中，按钮已禁用（双保险）
      if (get().recording) {
        // 已在录音：把小窗掀到前台 + 展开会议页
        recorderWin?.focus()
        useUiStore.getState().setMeetingOpen(true)
        return
      }
      const win = openRecorderWindow() // 必须在用户手势同步栈内
      if (!win) {
        useUiStore.getState().toast('无法打开录音小窗，请允许本站弹出式窗口', 'err')
        return
      }
      recorderWin = win
      useUiStore.getState().toast('录音已在独立小窗进行，关闭或刷新页面不影响录音')
    },

    async stop() {
      if (IS_RECORDER) {
        // ---- controller：真实停止 ----
        if (!get().recording) return
        set({ recording: false, finalizing: true, statusText: '正在结束录音…' })
        segStopFlag = true
        sessionStorage.removeItem(SESSION_ACTIVE_KEY)
        // 结清末段（等待 onstop 落定，8s 兜底）
        await new Promise<void>((resolve) => {
          if (segRecorder && segRecorder.state === 'recording') {
            segResolve = resolve
            segRecorder.stop()
            setTimeout(resolve, 8000)
          } else {
            resolve()
          }
        })
        segResolve = null
        if (segTimer) {
          clearTimeout(segTimer)
          segTimer = null
        }
        if (tickTimer) {
          clearInterval(tickTimer)
          tickTimer = null
        }
        // 结束长录音器 → 合成回放地址（5s 兜底）
        await new Promise<void>((resolve) => {
          const fr = fileRecorder
          if (fr && fr.state === 'recording') {
            fr.onstop = () => {
              const blob = new Blob(fileChunks, { type: fr.mimeType || 'audio/webm' })
              const prev = get().audioUrl
              if (blob.size > 0) {
                const url = URL.createObjectURL(blob)
                set({ audioUrl: url })
                // 持久化到 IndexedDB：小窗关闭后主页面仍可回放
                const audioId = typeof crypto !== 'undefined' && crypto.randomUUID
                  ? crypto.randomUUID()
                  : `${Date.now()}-${Math.random().toString(36).slice(2)}`
                set({ audioId })
                void saveAudio(audioId, blob).catch(() => {})
              } else {
                set({ audioUrl: null })
              }
              if (prev) URL.revokeObjectURL(prev)
              resolve()
            }
            fr.stop()
            setTimeout(resolve, 5000)
          } else {
            resolve()
          }
        })
        fileRecorder = null
        if (stream) {
          stream.getTracks().forEach((t) => t.stop())
          stream = null
        }
        // 有转写内容则自动生成最终纪要（先结清末段再生成，覆盖最后一句话）
        const text = transcriptText(get().transcript)
        if (text.trim()) {
          await summarize('final')
        } else {
          notify('未识别到有效语音')
        }
        set({ finalizing: false })
        return
      }
      // ---- mirror：转发给小窗；断连则无操作（died/超时已置 recording=false） ----
      if (controllerAlive()) send('cmd', { action: 'stop' })
    },

    reset() {
      if (get().recording) void get().stop()
      const prev = get().audioUrl
      if (prev) URL.revokeObjectURL(prev)
      const prevAudioId = get().audioId
      if (prevAudioId) void deleteAudio(prevAudioId).catch(() => {})
      runId += 1
      minutesBuf = ''
      if (IS_RECORDER) sessionStorage.removeItem(SESSION_ACTIVE_KEY)
      set({
        elapsed: 0,
        startedAt: null,
        audioUrl: null,
        audioId: null,
        error: null,
        transcript: [],
        transcriptError: null,
        minutesRaw: '',
        minutesMode: 'view',
        statusText: '',
      })
    },

    generateFinal() {
      if (!IS_RECORDER && controllerAlive()) {
        send('cmd', { action: 'generateFinal' })
        return
      }
      void summarize('final')
    },

    polish() {
      if (!IS_RECORDER && controllerAlive()) {
        send('cmd', { action: 'polish' })
        return
      }
      void summarize('polish')
    },

    async copyMinutes() {
      const text = get().minutesRaw
      if (!text) {
        useUiStore.getState().toast('暂无纪要内容可拷贝', 'err')
        return
      }
      try {
        await navigator.clipboard.writeText(text)
        useUiStore.getState().toast('纪要已拷贝到剪贴板', 'info')
      } catch {
        // clipboard API 不可用时降级
        const ta = document.createElement('textarea')
        ta.value = text
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        ta.remove()
        useUiStore.getState().toast('纪要已拷贝到剪贴板', 'info')
      }
    },

    setMinutesMode(m) {
      set({ minutesMode: m })
    },

    setMinutesRaw(t) {
      if (!IS_RECORDER && controllerAlive()) {
        send('cmd', { action: 'setMinutesRaw', value: t })
      }
      set({ minutesRaw: t })
    },

    async saveMeeting() {
      if (!IS_RECORDER && controllerAlive()) {
        send('cmd', { action: 'saveMeeting' })
        return
      }
      const content = get().minutesRaw
      if (!content.trim()) {
        useUiStore.getState().toast('暂无纪要内容可保存', 'err')
        return
      }
      set({ saving: true })
      try {
        await api<{ ok: boolean }>('POST', '/minutes/save', { title: '', content })
        useUiStore.getState().toast('会议纪要已保存到平台会议记录', 'info')
        notify('已保存到平台会议记录')
      } catch (e) {
        useUiStore.getState().toast(`保存失败：${e instanceof Error ? e.message : String(e)}`, 'err')
      } finally {
        set({ saving: false })
      }
    },
  }
})

/** 供 recorder 入口调用的连接 id（autostart 双开检测过滤自消息用） */
export const connectId = MY_ID
