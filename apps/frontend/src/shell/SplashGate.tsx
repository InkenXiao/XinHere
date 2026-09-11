// 开屏双门「背景窗口」：两张全屏背景图固定加载于开屏页，仅透过左右卡片内部可见（窗口机制见 theme.css 开屏区块）
// 默认交叉合拢，鼠标进入展开、离开收合；点击左门「向前放大铺满全屏」进入 Xin语，右门「向后缩小消失」进入 Xin台
// JS 只写入 --door-dx/--door-dy/--door-k 三个变量，card 与卡内 .door-art 的逆变换引用同一变量源
// （@property 注册过渡），动画中途任意时刻卡内画面都与真实全屏背景逐像素对齐，衔接无缝
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

  useEffect(() => {
    // 触屏适配：无 hover 能力的设备没有「鼠标进入展开」交互，常驻展开态让卡片直接可点
    if (window.matchMedia('(hover: none)').matches) setSplit(true)
  }, [])

  useEffect(
    () => () => {
      if (timerRef.current) window.clearTimeout(timerRef.current)
    },
    [],
  )

  const enter = (door: Door) => {
    if (entering) return
    // 写入三个动画变量（不再拼 --door-fx 变换字符串）：
    //   --door-dx/--door-dy：门卡中心移到屏幕中心的位移；--door-k：缩放倍率
    //   左门放大到溢出全屏（1.06 过扫描，保证圆角归零前已盖满），右门缩小到 0.26 后随透明度淡出
    const slot = door === 'tower' ? towerSlotRef.current : cockpitSlotRef.current
    const card = slot?.querySelector<HTMLElement>('.door-card')
    if (slot && card) {
      // 缩放倍率按「当前姿态的门框覆盖视口」计算（demo cover 公式）：
      // getBoundingClientRect 是旋转后的外接框，交叉未展开（触屏/快速点击）时直接量 rect
      // 会把旋转虚算进尺寸 → 缩放偏小 → 卡片盖不满视口两侧，露出白色面纱竖条
      const cs = getComputedStyle(slot)
      const rot = ((parseFloat(cs.getPropertyValue('--door-rot')) || 0) * Math.PI) / 180
      const cos = Math.abs(Math.cos(rot))
      const sin = Math.abs(Math.sin(rot))
      const halfW = card.offsetWidth / 2
      const halfH = card.offsetHeight / 2
      const cover =
        1.002 * Math.max(
          (Math.abs(innerWidth / 2) * cos + Math.abs(innerHeight / 2) * sin) / halfW,
          (Math.abs(innerWidth / 2) * sin + Math.abs(innerHeight / 2) * cos) / halfH,
        )
      const rect = card.getBoundingClientRect()
      const dx = innerWidth / 2 - (rect.left + rect.width / 2)
      const dy = innerHeight / 2 - (rect.top + rect.height / 2)
      const k =
        door === 'tower'
          ? Math.max(3.6, cover * 1.08) // demo: frameScale = max(3.6, cover*1.08) 过扫描
          : 0.26
      card.style.setProperty('--door-dx', `${dx}px`)
      card.style.setProperty('--door-dy', `${dy}px`)
      card.style.setProperty('--door-k', `${k}`)
    }
    setEntering(door)
    // 点击即切模式：目标层（Xin语/Xin台）立即在不透明开屏遮罩后方开始 crossfade 预热（用户不可见），
    // 卡片动画播放期间页面已加载就绪；动画结束整层淡出时，直接呈现完整的目标页面
    setMode(door)
    // 卸载时机对齐动效：卡片缩放 1.05s + 整层淡出 0.45s（0.95s 起）
    timerRef.current = window.setTimeout(onDone, 1500)
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
