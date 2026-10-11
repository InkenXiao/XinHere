// 对话附件共用小工具：输入区（ChatComposer）与消息气泡（ChatPanel）共用的
// 类型白名单 / 图标 / 体积格式化，与后端 services/chat_files.py 白名单对齐
export const ATTACH_ACCEPT = '.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg,.gif,.webp,.txt,.md'

const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp'])

/** 附件图标（按扩展名粗分：图片 / 表格 / 文档 / 文本） */
export function attIcon(name: string): string {
  const ext = name.slice(name.lastIndexOf('.')).toLowerCase()
  if (IMAGE_EXTS.has(ext)) return '🖼'
  if (ext === '.xls' || ext === '.xlsx') return '📊'
  if (ext === '.ppt' || ext === '.pptx') return '📑'
  if (ext === '.pdf') return '📕'
  if (ext === '.doc' || ext === '.docx') return '📘'
  return '📄'
}

/** 体积格式化：B/KB/MB 一位小数 */
export function fmtSize(size: number): string {
  if (size < 1024) return `${size}B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)}KB`
  return `${(size / 1024 / 1024).toFixed(1)}MB`
}
