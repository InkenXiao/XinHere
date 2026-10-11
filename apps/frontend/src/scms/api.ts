// scms 移植组件的 API 适配层：保持 A 组件 api(method, path, body, opts) 调用形状，
// 协议对接本仓 B 后端（REST 状态码 + {code,message} 错误体，无若依包封）。
// 返回值包成 { code: 200, msg: 'ok', data } 形状，组件 .data 取值不变。
import { runtimeEnv } from '@/config'

export const API_BASE: string = runtimeEnv.API_BASE || '/api/v1'
const TOKEN_KEY = 'xinhere.token'

export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}

export function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra }
  const t = getToken()
  if (t) h['Authorization'] = `Bearer ${t}`
  return h
}

export interface ApiResult<T = unknown> {
  code: number
  msg: string
  data: T
}

export class ApiError extends Error {
  code: string
  status: number
  constructor(code: string, message: string, status: number) {
    super(message)
    this.code = code
    this.status = status
  }
}

export interface ReqConfig {
  params?: Record<string, unknown>
  silent?: boolean
}

function qs(params?: Record<string, unknown>): string {
  if (!params) return ''
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.append(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export async function api<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
  opts?: ReqConfig,
): Promise<ApiResult<T>> {
  const res = await fetch(`${API_BASE}${path}${qs(opts?.params)}`, {
    method: method.toUpperCase(),
    headers: authHeaders(body !== undefined ? { 'Content-Type': 'application/json' } : undefined),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401) {
    // 与宿主一致：401 清理登录态回登录页
    try { localStorage.removeItem(TOKEN_KEY) } catch { /* ignore */ }
    window.location.reload()
    throw new ApiError('UNAUTHORIZED', '登录已失效，请重新登录', 401)
  }
  let payload: unknown = null
  try { payload = await res.json() } catch { payload = null }
  if (!res.ok) {
    const body_ = (payload ?? {}) as { code?: string; message?: string }
    throw new ApiError(body_.code || 'UPSTREAM_ERROR', body_.message || `请求失败(${res.status})`, res.status)
  }
  return { code: 200, msg: 'ok', data: payload as T }
}

/** 附件下载（blob 不走 json 收口） */
export async function downloadFile(path: string, fileName: string): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, { headers: authHeaders() })
  if (!res.ok) return
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName
  a.click()
  URL.revokeObjectURL(url)
}
