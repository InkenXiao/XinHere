// 动态填报模板模块类型：契约生成物 re-export + iframe postMessage 协议
import type {
  DynamicFillDataItem,
  DynamicTemplateItem,
  DynamicTemplateStatus,
  ParseFieldDef,
  ParseResult,
  DynamicFillPage,
} from '@contracts/events'

export type { DynamicFillDataItem, DynamicTemplateItem, DynamicTemplateStatus, ParseFieldDef, ParseResult }

/** 字段类型中文（与后端 fill_form.j2 同映射） */
export const FIELD_TYPE_ZH: Record<ParseFieldDef['type'], string> = {
  text: '单行文本',
  textarea: '多行文本',
  number: '数字',
  date: '日期',
  select: '下拉选择',
  table: '表格',
}

export const FIELD_TYPES: ParseFieldDef['type'][] = ['text', 'textarea', 'number', 'date', 'select']

/** 填报页接口返回（GET /dynamic-templates/{id}/fill，iframe srcdoc 用） */
export type FillPage = DynamicFillPage

/** iframe ↔ 父页 postMessage 协议（fill_form.j2 内嵌 JS 同款） */
export type XhIframeMessage =
  | { type: 'xinhere-ready'; height: number }
  | { type: 'xinhere-draft'; data: Record<string, unknown> }
  | { type: 'xinhere-submit'; data: Record<string, unknown> }
  | { type: 'xinhere-cancel' }
