// 按钮原子
import type { ButtonHTMLAttributes, ReactNode } from 'react'

export default function Button({
  variant = 'ghost',
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost'; children: ReactNode }) {
  return (
    <button className={variant === 'primary' ? 'btn-primary' : 'btn-ghost'} {...rest}>
      {children}
    </button>
  )
}
