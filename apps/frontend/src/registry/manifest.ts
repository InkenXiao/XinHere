// 组件静态清单：9 个插件构建期定死（Assembler 唯一来源；未来迁移插件仓只改本文件）
import type { ComponentDefinition } from './types'
import cashGuaranteeForm from '@/plugins/CashGuaranteeForm'
import fileRecordCard from '@/plugins/FileRecordCard'
import kanbanCard from '@/plugins/KanbanCard'
import kpiFillForm from '@/plugins/KpiFillForm'
import lampAdjustPanel from '@/plugins/LampAdjustPanel'
import msFeedbackForm from '@/plugins/MsFeedbackForm'
import pitReportView from '@/plugins/PitReportView'
import reportConfirmCard from '@/plugins/ReportConfirmCard'
import riskDispatchConfirm from '@/plugins/RiskDispatchConfirm'
import riskFillForm from '@/plugins/RiskFillForm'

export const componentDefs: ComponentDefinition[] = [
  cashGuaranteeForm,
  fileRecordCard,
  kanbanCard,
  kpiFillForm,
  lampAdjustPanel,
  msFeedbackForm,
  pitReportView,
  reportConfirmCard,
  riskDispatchConfirm,
  riskFillForm,
]
