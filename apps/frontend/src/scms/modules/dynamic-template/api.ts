// 动态填报模板模块 API 封装（统一走 @/api/request，包封解包取 .data）
import { api } from '@/scms/api'
import type { TaskBoard, TodoDetail } from '@/scms/types'
import type { DynamicTemplateItem, DynamicTemplateStatus, FillPage, ParseFieldDef, ParseResult } from './types'

export interface DynamicTemplateDetail extends DynamicTemplateItem {
  columns: ParseFieldDef[]
}

/** Excel 上传前后缀校验（与后端 _EXCEL_SUFFIX 一致） */
export const EXCEL_SUFFIXES = ['.xls', '.xlsx', '.xlsm']

export function isExcelFile(f: File): boolean {
  const n = f.name.toLowerCase()
  return EXCEL_SUFFIXES.some((s) => n.endsWith(s))
}

/** Excel 解析预览（不落库）：首 Sheet + 首行表头 → 字段推断 + 样本行 */
export async function parseExcel(file: File): Promise<ParseResult> {
  const fd = new FormData()
  fd.append('file', file)
  const r = await api<ParseResult>('POST', '/dynamic-templates/parse-excel', fd)
  return r.data
}

/** 由 Excel 创建动态模板（创建即启用，出现在派发下拉）；columns 为预览页调整后的解析结果 */
export async function createFromExcel(
  file: File,
  name: string,
  parse: ParseResult,
): Promise<DynamicTemplateItem> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('name', name)
  fd.append('columns_json', JSON.stringify(parse))
  const r = await api<DynamicTemplateItem>('POST', '/dynamic-templates/from-excel', fd)
  return r.data
}

export async function parseExcels(files: File[]): Promise<ParseResult> {
  const fd = new FormData()
  files.forEach((file) => fd.append('files', file))
  return (await api<ParseResult>('POST', '/dynamic-templates/parse-excels', fd, { silent: true })).data
}

export async function createFromExcels(files: File[], name: string, parse: ParseResult): Promise<DynamicTemplateItem> {
  const fd = new FormData()
  files.forEach((file) => fd.append('files', file))
  fd.append('name', name)
  fd.append('columns_json', JSON.stringify(parse))
  return (await api<DynamicTemplateItem>('POST', '/dynamic-templates/from-excels', fd)).data
}

/** 管理列表：系统级共享 + 本人私有（含停用） */
export async function listTemplates(): Promise<DynamicTemplateItem[]> {
  const r = await api<{ items: Array<DynamicTemplateItem & { columns?: ParseFieldDef[] }> }>('GET', '/dynamic-templates', undefined, { silent: true })
  return r.data.items.map((item) => ({
    ...item,
    parse_result: item.parse_result ?? {
      columns: item.columns ?? [],
      tabs: item.tabs ?? [],
    },
  }))
}

/** 启用 / 停用 */
export async function patchTemplateStatus(templateId: string, status: DynamicTemplateStatus): Promise<void> {
  await api('PATCH', `/dynamic-templates/${templateId}/status`, { status })
}

/** 逻辑删除 */
export async function removeTemplate(templateId: string): Promise<void> {
  await api('DELETE', `/dynamic-templates/${templateId}`)
}

/** 填报页 HTML（todo_id 有值且已填过 → 预填，退回重填场景） */
export async function getFillPage(templateId: string, todoId?: string): Promise<FillPage> {
  const q = todoId ? `?todo_id=${encodeURIComponent(todoId)}` : ''
  const r = await api<FillPage>('GET', `/dynamic-templates/${templateId}/fill${q}`)
  return r.data
}

/** 派发人任务级审核：一次加载同一任务下全部办理人的填报数据。 */
export async function getTaskFillReview(taskId: string, templateId: string): Promise<{
  board: TaskBoard
  todos: TodoDetail[]
  template: DynamicTemplateDetail
}> {
  const [board, template] = await Promise.all([
    api<TaskBoard>('GET', `/tasks/${taskId}/board`),
    api<DynamicTemplateDetail>('GET', `/dynamic-templates/${templateId}`),
  ])
  const items = Object.values(board.data.groups).flat()
  const todos = await Promise.all(items.map((item) => api<TodoDetail>('GET', `/todos/${item.todo_id}`)))
  return { board: board.data, todos: todos.map((item) => item.data), template: template.data }
}

/** 填报提交：dynamic_fill_data 落库 + 待办转两段式（review_required → 待审） */
export async function submitFill(
  templateId: string,
  todoId: string,
  data: Record<string, unknown>,
): Promise<unknown> {
  const r = await api('POST', `/dynamic-templates/${templateId}/fill`, { todo_id: todoId, data })
  return r.data
}

/** 暂存未完成填报，不校验必填项、不改变待办状态。 */
export async function saveFillDraft(
  templateId: string,
  todoId: string,
  data: Record<string, unknown>,
): Promise<unknown> {
  const r = await api('POST', `/dynamic-templates/${templateId}/fill/draft`, { todo_id: todoId, data })
  return r.data
}
