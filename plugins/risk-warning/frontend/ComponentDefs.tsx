// risk-warning 前端组件契约入口：目录即注册（registry/manifest.ts 经 import.meta.glob 聚合）
import type { ComponentDefinition } from '@app/registry/types'
import cashGuaranteeForm from './CashGuaranteeForm'
import riskDispatchConfirm from './RiskDispatchConfirm'
import riskFillForm from './RiskFillForm'

export default [riskDispatchConfirm, riskFillForm, cashGuaranteeForm] as ComponentDefinition[]
