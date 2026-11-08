// post-investment 前端组件契约入口：目录即注册（registry/manifest.ts 经 import.meta.glob 聚合）
import type { ComponentDefinition } from '@app/registry/types'
import kanbanCard from './KanbanCard'
import pitReportView from './PitReportView'

export default [pitReportView, kanbanCard] as ComponentDefinition[]
