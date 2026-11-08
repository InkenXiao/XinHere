// 组件注册聚合器：目录即契约 —— 扫描仓库 plugins/*/frontend/ComponentDefs.tsx 聚合注册，
// 与后端 loader 扫描 plugins/*/backend 对称；新增插件前端无需改本文件（构建期静态展开）。
import type { ComponentDefinition } from './types'

const modules = import.meta.glob<{ default: ComponentDefinition[] }>(
  '../../../../plugins/*/frontend/ComponentDefs.tsx',
  { eager: true },
)

export const componentDefs: ComponentDefinition[] = Object.values(modules).flatMap(
  (m) => m.default,
)
