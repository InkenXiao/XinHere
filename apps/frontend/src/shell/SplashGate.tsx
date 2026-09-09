// 开屏双门：参照 XinHere-offline 设计稿 door-entry —— 瞭望塔(Xin语)/驾驶舱(Xin台) 双门卡片
// 默认交叉合拢，鼠标进入展开、离开收合；点击左门「向前放大」进入 Xin语，右门「缩小」进入 Xin台
import { useEffect, useRef, useState } from 'react'
import { useUiStore } from '@/state/uiStore'

type Door = 'tower' | 'cockpit'

export default function SplashGate({ onDone }: { onDone: () => void }) {
  const setMode = useUiStore((s) => s.setMode)
  const [split, setSplit] = useState(false)
  const [entering, setEntering] = useState<Door | null>(null)
  const timerRef = useRef<number | null>(null)
  const towerSlotRef = useRef<HTMLDivElement>(null)
  const cockpitSlotRef = useRef<HTMLDivElement>(null)

  useEffect(
    () => () => {
      if (timerRef.current) window.clearTimeout(timerRef.current)
    },
    [],
  )

  const enter = (door: Door) => {
    if (entering) return
    // 计算门卡到达屏幕中心的目标变换：左门放大铺满全屏（向前穿越），右门缩小退缩（向纵深远去）
    const slot = door === 'tower' ? towerSlotRef.current : cockpitSlotRef.current
    const card = slot?.querySelector<HTMLElement>('.door-card')
    if (card) {
      const rect = card.getBoundingClientRect()
      const vw = window.innerWidth
      const vh = window.innerHeight
      const dx = vw / 2 - (rect.left + rect.width / 2)
      const dy = vh / 2 - (rect.top + rect.height / 2)
      const fx =
        door === 'tower'
          ? `translate(${dx}px, ${dy}px) scale(${Math.max(vw / rect.width, vh / rect.height) * 1.05})`
          : `translate(${dx}px, ${dy}px) scale(0.3)`
      card.style.setProperty('--door-fx', fx)
    }
    setEntering(door)
    setMode(door)
    timerRef.current = window.setTimeout(onDone, 1050)
  }

  return (
    <div
      id="door-entry"
      className={`${split ? 'split' : ''} ${entering ? `is-entering is-${entering === 'tower' ? 'forward' : 'back'}` : ''}`}
      onMouseEnter={() => !entering && setSplit(true)}
      onMouseLeave={() => !entering && setSplit(false)}
    >
      {/* 面纱：进入动效期间淡出，露出底下真实首页 */}
      <span className="door-veil" aria-hidden="true" />
      <div id="door-copy">
        <div className="door-logo">
          <svg width="1059" height="1059" viewBox="0 0 1059 1059" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path
              d="M623.936 551.363C625.23 550.958 626.553 551.919 626.654 553.271C628.525 578.421 655.029 614.532 666.675 630.786C705.173 684.513 770.477 772.029 793.479 799.545C829.41 842.526 847.374 874.763 886.898 899.836V907H656.941V899.836C689.278 867.6 677.301 843.721 667.719 824.618C645.019 789.418 586.138 707.566 552.355 661.2C550.383 658.494 546.426 658.425 544.429 661.112C524.413 688.041 456.363 781.579 428.608 824.618C419.026 843.721 407.049 867.6 439.386 899.836V907H209.429V899.836C248.953 874.763 270.914 844.621 303.678 801.554C317.635 784.858 338.123 758.448 359.667 726.901C373.63 706.455 383.721 694.84 398.86 673.712C404.848 665.354 432.5 628.5 451.5 615.5C470.499 602.5 490.052 591.398 515 583C539.148 574.871 596.585 559.929 623.936 551.363ZM419.397 227.192C407.45 229.577 394.309 252.223 397.893 277.252C399.741 290.155 420.52 319.661 437.317 345.188C479.665 409.55 537.669 488.215 577.092 541.85C548.404 549.864 483.564 568.577 452.029 591.543C450.604 592.581 448.55 591.468 448.547 589.705C448.536 580.768 445.147 572.112 426.565 545.425C398.752 505.481 315.462 385.821 265.287 313.008C252.145 293.938 215.111 247.216 172.103 227.192V216.466H419.397V227.192ZM821.718 151.146C795.511 214.706 772.117 289.253 764.652 333.111C757.187 376.969 732.924 424.56 717.994 446.022C703.063 467.485 657.339 513.21 587.777 537.479C587.777 537.479 582.903 531.414 579.333 526.315C569.128 511.74 554.696 492.68 554.696 492.68C681.605 446.022 676.002 370.437 721.2 299.921C696.531 310.715 659.206 360.172 643.477 382.899C627.749 405.627 585.992 453.568 544.431 460.953C555.816 336.542 689.721 251.966 733.058 231.753C769.383 214.81 805.638 180.138 821.718 151.146Z"
              fill="url(#door-logo-grad)"
            />
            <defs>
              <linearGradient id="door-logo-grad" x1="663.272" y1="175.108" x2="663.272" y2="909.5" gradientUnits="userSpaceOnUse">
                <stop stopColor="#90D09E" />
                <stop offset="1" stopColor="#2D98C1" />
              </linearGradient>
            </defs>
          </svg>
        </div>
        <h1>XinHere</h1>
        <p>信在此，新在此</p>
      </div>

      <div
        ref={towerSlotRef}
        className={`door-slot door-slot--tower ${entering === 'tower' ? 'enter-forward' : entering ? 'exit' : ''}`}
        role="button"
        tabIndex={0}
        aria-label="进入瞭望塔"
        onClick={() => enter('tower')}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            enter('tower')
          }
        }}
      >
        <div className="door-card">
          <div className="door-art door-art--tower" />
          <div className="door-tag">攻 · 面向未来</div>
          <div className="door-name">
            <b>瞭望塔</b>
            <span>WATCHTOWER</span>
          </div>
        </div>
      </div>

      <div
        ref={cockpitSlotRef}
        className={`door-slot door-slot--cockpit ${entering === 'cockpit' ? 'enter-back' : entering ? 'exit' : ''}`}
        role="button"
        tabIndex={0}
        aria-label="进入驾驶舱"
        onClick={() => enter('cockpit')}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            enter('cockpit')
          }
        }}
      >
        <div className="door-card">
          <div className="door-art door-art--cockpit" />
          <div className="door-tag">守 · 立足当下</div>
          <div className="door-name">
            <b>驾驶舱</b>
            <span>COCKPIT</span>
          </div>
        </div>
      </div>
    </div>
  )
}
