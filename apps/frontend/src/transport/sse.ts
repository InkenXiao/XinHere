// SSE 传输层：EventSource 不支持 POST，用 fetch + ReadableStream 手写解析
// 帧格式：event: <type> / data: <json 含 seq,time> / id: <session_id:seq>
import { API_BASE, authHeaders, mockEnabled, ApiError } from './api'
import type { PlatformEvent } from '@/types'

export interface SseFrame {
  event: string
  data: string
  id?: string
}

/** 逐帧解析 SSE 字节流 */
export async function* parseSse(stream: ReadableStream<Uint8Array>): AsyncGenerator<SseFrame> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      // 统一换行，简化帧边界判定
      buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
      let idx: number
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        let event = 'message'
        let id: string | undefined
        const dataLines: string[] = []
        for (const line of raw.split('\n')) {
          if (!line || line.startsWith(':')) continue
          const ci = line.indexOf(':')
          const field = ci < 0 ? line : line.slice(0, ci)
          let val = ci < 0 ? '' : line.slice(ci + 1)
          if (val.startsWith(' ')) val = val.slice(1)
          if (field === 'event') event = val
          else if (field === 'data') dataLines.push(val)
          else if (field === 'id') id = val
        }
        if (dataLines.length > 0) yield { event, data: dataLines.join('\n'), id }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/** SSE 帧 → 平台事件（data JSON 中 seq/time 提升，其余为 payload） */
export function frameToEvent(frame: SseFrame): PlatformEvent | null {
  let parsed: Record<string, unknown>
  try {
    parsed = JSON.parse(frame.data) as Record<string, unknown>
  } catch {
    return null
  }
  const { seq, time, ...payload } = parsed
  return {
    seq: typeof seq === 'number' ? seq : -1,
    type: frame.event,
    time: typeof time === 'string' ? time : new Date().toISOString(),
    data: payload,
  }
}

export interface SseHandlers {
  onEvent: (evt: PlatformEvent) => void
  onStatus?: (s: 'open' | 'reconnecting' | 'closed') => void
}

async function readStream(res: Response, handlers: SseHandlers): Promise<void> {
  if (!res.ok || !res.body) {
    let code = 'INTERNAL'
    let message = `流请求失败（${res.status}）`
    try {
      const j = (await res.json()) as { code?: string; message?: string }
      if (j.code) code = j.code
      if (j.message) message = j.message
    } catch {
      /* ignore */
    }
    throw new ApiError(code, message, res.status, res.headers.get('X-Request-Id') || undefined)
  }
  handlers.onStatus?.('open')
  for await (const frame of parseSse(res.body)) {
    const evt = frameToEvent(frame)
    if (evt) handlers.onEvent(evt)
  }
}

/** POST chat → SSE 流（一次 run） */
export async function streamChat(
  sessionId: string,
  body: {
    message: string
    kb_ids?: string[]
    web_search?: boolean
    model?: string
    file_names?: string[]
    skill?: string // 技能市场技能名：本轮对话加载并执行该技能
  },
  handlers: SseHandlers,
  signal?: AbortSignal,
): Promise<void> {
  if (mockEnabled()) {
    const m = await import('@/mocks/server')
    return m.mockSse(sessionId, { kind: 'chat', body }, handlers)
  }
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/chat`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json', Accept: 'text/event-stream' }),
    body: JSON.stringify(body),
    signal,
  })
  await readStream(res, handlers)
}

/** GET events SSE 形态续流（断线重连：baseline 帧 + after_seq 增量） */
export async function resumeEvents(
  sessionId: string,
  afterSeq: number,
  lastEventId: string | null,
  handlers: SseHandlers,
  signal?: AbortSignal,
): Promise<void> {
  if (mockEnabled()) {
    const m = await import('@/mocks/server')
    return m.mockSse(sessionId, { kind: 'events', afterSeq }, handlers)
  }
  const headers = authHeaders({ Accept: 'text/event-stream' })
  if (lastEventId) headers['Last-Event-ID'] = lastEventId
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/events?after_seq=${afterSeq}`, {
    method: 'GET',
    headers,
    signal,
  })
  await readStream(res, handlers)
}

/** 会议纪要流式生成（阶段/最终/润色）：data 帧为 {"delta": "..."} / {"done": true} */
export async function streamMinutes(
  body: { transcript: string; mode: 'stage' | 'final' | 'polish'; instruction?: string },
  handlers: { onDelta: (d: string) => void; onDone: () => void },
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/minutes/summarize`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json', Accept: 'text/event-stream' }),
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) {
    handlers.onDelta('\n[纪要生成失败] 服务暂不可用')
    handlers.onDone()
    return
  }
  for await (const frame of parseSse(res.body)) {
    let payload: { delta?: string; done?: boolean } = {}
    try {
      payload = JSON.parse(frame.data || '{}')
    } catch {
      continue
    }
    if (payload.delta) handlers.onDelta(payload.delta)
    if (payload.done) break
  }
  handlers.onDone()
}
