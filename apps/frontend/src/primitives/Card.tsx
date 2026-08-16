// 卡片原子
import type { ReactNode } from 'react'

export default function Card({ title, children, extra }: { title?: ReactNode; children: ReactNode; extra?: ReactNode }) {
  return (
    <div className="dash-card">
      {title && (
        <h3>
          {title}
          {extra}
        </h3>
      )}
      {children}
    </div>
  )
}
