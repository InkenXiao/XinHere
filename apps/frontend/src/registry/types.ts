// 组件注册表类型（docs/design/04 §3）
import type { ComponentType } from 'react'
import type { PlatformEvent } from '@/types'

export type MatchRole = 'start' | 'update' | 'end'

export interface Matched {
  id: string
  role: MatchRole
}

/** 框架注入组件的交互通道（REST 封装，落事件后由 SSE 回推折叠） */
export interface ComponentEmit {
  update: (draft: Record<string, unknown>) => Promise<void>
  submit: (action: 'submit' | 'cancel', values?: Record<string, unknown>) => Promise<void>
}

/** 组件基座：component/request 或事件族 start 建立 */
export interface ComponentBase {
  component_id: string
  kind: string
  kind_version: number
  interrupt_id?: string
  props: Record<string, unknown>
  status: 'open' | 'submitted' | 'cancelled'
}

export interface ComponentProps<S = unknown> {
  base: ComponentBase
  state: S
  emit: ComponentEmit
  disabled?: boolean
}

export interface ComponentDefinition<S = any> {
  kind: string
  version: number
  /** 只看当前事件；命中返回业务 id 与角色 */
  match: (evt: PlatformEvent) => Matched | null
  /** component/request 建基座时初始化业务态 */
  init?: (base: ComponentBase) => S
  /** 从 props 提取事件族别名 id（如 form_id），使家族事件能折叠进同组件 */
  aliases?: (props: Record<string, unknown>) => string[]
  /** 纯函数折叠；返回值必非空 */
  reduce: (state: S | undefined, evt: PlatformEvent, m: Matched) => S
  component: ComponentType<ComponentProps<S>>
}
