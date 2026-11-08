// kpi-assessment 前端组件契约入口：目录即注册（registry/manifest.ts 经 import.meta.glob 聚合）
import type { ComponentDefinition } from '@app/registry/types'
import kpiFillForm from './KpiFillForm'
import lampAdjustPanel from './LampAdjustPanel'
import msFeedbackForm from './MsFeedbackForm'

export default [kpiFillForm, lampAdjustPanel, msFeedbackForm] as ComponentDefinition[]
