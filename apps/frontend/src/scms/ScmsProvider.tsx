// scms 移植组件的 antd 隔离层：
// - prefixCls="ant-scms"：类名根隔离，不与宿主样式冲突
// - getPopupContainer → #scms-portal：下拉/浮层不挂 body，保持在应用层内
// - Modal 类组件经 getContainer 由各组件内指定（antd 无全局 Modal 容器配置），
//   其类名已被前缀隔离，不产生样式污染
import '@ant-design/v5-patch-for-react-19'
import { ConfigProvider } from 'antd'
import type { ReactNode } from 'react'

export function portalEl(): HTMLElement {
  return (document.getElementById('scms-portal') as HTMLElement) ?? document.body
}

export default function ScmsProvider({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider
      prefixCls="ant-scms"
      iconPrefixCls="ant-scmsicon"
      getPopupContainer={portalEl}
    >
      {children}
    </ConfigProvider>
  )
}
