// KB 文件代理链路（参考来源下载 / 预览弹窗）共用拉流逻辑：
// 带平台鉴权头拉取文件流。网关对业务错误可能返回 HTTP 200 + JSON
// （如 {"code":403,"error":"知识资源行为权限不足：DOWNLOAD"}），
// 此时不能把 JSON 当文件保存/渲染，解析原文案后抛 KbFileError 供调用方直接提示。
import { API_BASE, authHeaders } from '@/scms/api'

/** 网关/HTTP 业务错误：message 为面向用户的文案（区别于网络异常与渲染异常） */
export class KbFileError extends Error {}

/** 带平台鉴权头拉取 KB 文件流；HTTP 非 2xx 或 200+JSON 业务错误时抛 KbFileError */
export async function fetchKbBlob(url: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}${url}`, { headers: authHeaders() })
  if (!res.ok) {
    throw new KbFileError(res.status === 403 ? '您没有该文件的访问权限' : `文件获取失败（HTTP ${res.status}）`)
  }
  const ctype = (res.headers.get('content-type') || '').toLowerCase()
  if (ctype.includes('application/json')) {
    throw new KbFileError(await jsonErrorMsg(res))
  }
  return res.blob()
}

/** 200+JSON 业务错误体 → 面向用户的文案；解析失败回落默认文案 */
async function jsonErrorMsg(res: Response): Promise<string> {
  try {
    const d = (await res.json()) as { error?: string; msg?: string }
    return d?.error || d?.msg || '文件获取失败，请稍后重试'
  } catch {
    return '文件获取失败，请稍后重试'
  }
}
