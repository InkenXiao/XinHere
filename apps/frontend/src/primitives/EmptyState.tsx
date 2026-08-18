// 空状态原子：无数据页面/未开放区域统一展示（提示文案默认「开发中，敬请期待」）
export default function EmptyState({
  title = '开发中，敬请期待',
  hint,
  icon = '🚧',
}: {
  title?: string
  hint?: string
  icon?: string
}) {
  return (
    <div className="empty-state">
      <div className="es-ico">{icon}</div>
      <div className="es-title">{title}</div>
      {hint && <div className="es-hint">{hint}</div>}
    </div>
  )
}
