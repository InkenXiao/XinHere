// REST 统一封装：Bearer 注入、X-Request-Id 透出、{code,message} 错误体解析、401 登出
// 所有 /api/v1 请求必须经此模块，禁组件内裸 fetch
import { runtimeEnv } from '@/config'

export const API_BASE: string = runtimeEnv.API_BASE || '/api/v1'

const TOKEN_KEY = 'xinhere.token'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  code: string
  status: number
  requestId?: string
  details?: unknown
  constructor(code: string, message: string, status: number, requestId?: string, details?: unknown) {
    super(message)
    this.code = code
    this.status = status
    this.requestId = requestId
    this.details = details
  }
}

let unauthorizedHandler: (() => void) | null = null
/** authStore 注册 401 处理（登出） */
export function onUnauthorized(fn: () => void) {
  unauthorizedHandler = fn
}

/** 本地 mock 开关：运行时配置 VITE_MOCK=1 / 构建期 VITE_MOCK=1 或 URL ?mock=1，默认关 */
export function mockEnabled(): boolean {
  if (runtimeEnv.MOCK === '1') return true
  try {
    return new URLSearchParams(window.location.search).has('mock')
  } catch {
    return false
  }
}

export function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra }
  const t = getToken()
  if (t) h['Authorization'] = `Bearer ${t}`
  return h
}

async function realRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers = authHeaders()
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError('UPSTREAM_ERROR', '网络异常，请稍后重试', 0)
  }
  const requestId = res.headers.get('X-Request-Id') || undefined
  if (res.status === 401) {
    unauthorizedHandler?.()
    throw new ApiError('UNAUTHORIZED', '登录已失效，请重新登录', 401, requestId)
  }
  if (!res.ok) {
    let code = 'INTERNAL'
    let message = `请求失败（${res.status}）`
    let details: unknown
    try {
      const j = (await res.json()) as { code?: string; message?: string; details?: unknown }
      if (j.code) code = j.code
      if (j.message) message = j.message
      details = j.details
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new ApiError(code, message, res.status, requestId, details)
  }
  return (await res.json()) as T
}

export async function api<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  if (mockEnabled()) {
    const m = await import('@/mocks/server')
    return m.mockApi<T>(method, path, body)
  }
  return realRequest<T>(method, path, body)
}

export const get = <T = unknown>(path: string) => api<T>('GET', path)
export const post = <T = unknown>(path: string, body?: unknown) => api<T>('POST', path, body)
export const put = <T = unknown>(path: string, body?: unknown) => api<T>('PUT', path, body)
