// A 弹窗原子（类名命名空间化 scms-modal-*，样式隔离不与宿主 .modal 冲突）
import type { CSSProperties, ReactNode } from 'react'

export default function ScmsModal({
  title, onClose, children, footer, closable = true, closeOnMask = true,
  centered = false, width, bodyMaxHeight, light = false,
}: {
  title: ReactNode
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  closable?: boolean
  closeOnMask?: boolean
  centered?: boolean
  width?: CSSProperties['width']
  bodyMaxHeight?: CSSProperties['maxHeight']
  light?: boolean
}) {
  return (
    <div
      className={`scms-modal-mask${light ? ' light' : ''}`}
      style={centered ? { alignItems: 'center' } : undefined}
      onClick={() => closable && closeOnMask && onClose()}
    >
      <div className={`scms-modal${light ? ' light' : ''}`} style={width ? { width, maxWidth: '96vw' } : undefined}>
        <div className="scms-modal-hd">
          <b>{title}</b>
          {closable && (
            <button className="x" onClick={onClose} aria-label="关闭">×</button>
          )}
        </div>
        <div className="scms-modal-bd" style={bodyMaxHeight ? { maxHeight: bodyMaxHeight } : undefined}>{children}</div>
        {footer && <div className="scms-modal-ft">{footer}</div>}
      </div>
    </div>
  )
}
