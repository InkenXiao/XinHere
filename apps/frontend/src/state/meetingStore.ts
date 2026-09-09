// 实时会议 · 分段录音 + 增量转写 + 阶段/最终纪要 + 润色 + 保存
// MediaRecorder 持有在模块级：关闭页面仅收起 UI，录音/转写/纪要后台继续
// 分段方式（关键）：每 SEGMENT_MS 销毁并重建 recorder，每段都是含完整头的独立音频文件
import { create } from 'zustand'
import { api, apiUpload } from '@/transport/api'
import { streamMinutes } from '@/transport/sse'
import type { MeetingLine } from '@/types'
import { useUiStore } from './uiStore'

const SEGMENT_MS = 2000 // 分段长度：延时 ≈ 段长 + 转写耗时 ≈ 2~3s
const SUMMARY_MS = 60000 // 每 60s 自动生成一次阶段纪要（且有 ≥30 字新增转写时）

interface MeetingState {
  recording: boolean // 录音中（后台持续，与会议页开关无关）
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
  audioUrl: string | null
  error: string | null
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

// ---------- 模块级单例（不随组件挂载/卸载重置） ----------
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

function fmtSec(sec: number) {
  const m = Math.floor(sec / 60)
  const s = Math.max(0, Math.floor(sec % 60))
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function transcriptText(lines: MeetingLine[]) {
  return lines.map((l) => `[${fmtSec(l.sec)}] ${l.text}`).join('\n')
}

export const useMeetingStore = create<MeetingState>((set, get) => {
  const notify = (t: string) => set({ statusText: t })

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

  return {
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
    error: null,

    async start() {
      if (get().recording || get().finalizing) return
      const prevUrl = get().audioUrl
      if (prevUrl) URL.revokeObjectURL(prevUrl)
      set({
        error: null,
        transcriptError: null,
        transcript: [],
        minutesRaw: '',
        minutesMode: 'view',
        elapsed: 0,
        startedAt: null,
        audioUrl: null,
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
      set({ recording: true, startedAt: Date.now(), statusText: '正在录音，收起页面后录音继续进行' })
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
    },

    async stop() {
      if (!get().recording) return
      set({ recording: false, finalizing: true, statusText: '正在结束录音…' })
      segStopFlag = true
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
            set({ audioUrl: blob.size > 0 ? URL.createObjectURL(blob) : null })
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
    },

    reset() {
      if (get().recording) void get().stop()
      const prev = get().audioUrl
      if (prev) URL.revokeObjectURL(prev)
      runId += 1
      minutesBuf = ''
      set({
        elapsed: 0,
        startedAt: null,
        audioUrl: null,
        error: null,
        transcript: [],
        transcriptError: null,
        minutesRaw: '',
        minutesMode: 'view',
        statusText: '',
      })
    },

    generateFinal() {
      void summarize('final')
    },

    polish() {
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
      set({ minutesRaw: t })
    },

    async saveMeeting() {
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
