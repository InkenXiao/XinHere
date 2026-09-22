// 开屏双门「背景窗口」：两张全屏背景图固定加载于开屏页，仅透过左右卡片内部可见（窗口机制见 theme.css 开屏区块）
// 默认交叉合拢，鼠标进入展开、离开收合；点击左门「向前放大铺满全屏」进入 Xin语，右门「向后缩小消失」进入 Xin台
// 双门展开（split）后，底部亮起全屏氛围光，青山知识库以「能量裂隙」（题字 + 发光线）自水面浮出；
// 点击裂隙开屏整体由下向上推移，进入第三页面（样式见 theme.css qingshan 区块）
import { useEffect, useRef, useState, type RefObject } from 'react'
import { useUiStore } from '@/state/uiStore'

type Door = 'tower' | 'cockpit' | 'qingshan'

export default function SplashGate({ onDone }: { onDone: () => void }) {
  const setMode = useUiStore((s) => s.setMode)
  const [split, setSplit] = useState(false)
  const [entering, setEntering] = useState<Door | null>(null)
  const timerRef = useRef<number | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  // 两个门 slot 的 ref：进入动效时测量姿态/尺寸，计算放大位移与缩放倍率
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
    // 青山知识库：跳过门卡形变测量，setMode 触发第三层自下向上推进，开屏整体同步上移退场
    if (door === 'qingshan') {
      setEntering(door)
      setMode(door)
      timerRef.current = window.setTimeout(onDone, 1200)
      return
    }
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
      // 写入门卡动画变量：card 的过渡引用变量源（@property 注册），动画平滑推进
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

  // 门渲染：slot 承担交互与姿态变换，card 内的窗口画面与视口锁死（art 逆变换见 theme.css）
  const renderDoor = (doorType: 'tower' | 'cockpit', slotRef: RefObject<HTMLDivElement | null>) => {
    const isTower = doorType === 'tower'
    const enteringClass = entering === doorType ? (isTower ? 'enter-forward' : 'enter-back') : entering ? 'exit' : ''
    return (
      <div
        ref={slotRef}
        className={`door-slot door-slot--${doorType} ${enteringClass}`}
        role="button"
        tabIndex={0}
        aria-label={`进入${isTower ? '瞭望塔' : '驾驶舱'}`}
        onClick={() => enter(doorType)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            enter(doorType)
          }
        }}
      >
        <div className="door-card">
          <div className={`door-art door-art--${doorType}`} />
          <div className="door-tag">{isTower ? '攻 · 面向未来' : '守 · 立足当下'}</div>
          <div className="door-name">
            <b>{isTower ? '瞭望塔' : '驾驶舱'}</b>
            <span></span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div
      ref={rootRef}
      id="door-entry"
      className={`${split ? 'split' : ''} ${entering ? `is-entering is-${entering === 'tower' ? 'forward' : entering === 'qingshan' ? 'qingshan' : 'back'}` : ''}`}
      onMouseEnter={() => !entering && setSplit(true)}
      onMouseLeave={() => !entering && setSplit(false)}
    >
      {/* 面纱：进入动效期间淡出，露出底下真实首页 */}
      <span className="door-veil" aria-hidden="true" />
      <div id="door-copy">
        {/* 完整品牌 logo（V 形标识 + XinHere 字标），替代原先的内联图标 + h1 组合 */}
        <div className="door-logo">
          <img src="/assets/start.svg" alt="XinHere" />
        </div>
        <p>信在此，新在此</p>
      </div>

      {/* 底部氛围光：双门展开后从水线向上弥散的全屏青绿微光，融合场景（纯展示，不拦截交互） */}
      <div className="qingshan-amblight" aria-hidden="true" />

      {/* 山形：水墨晕染山水一体——远山淡影、中景低山、主山三峰三层墨色递进，
          峰体点苔、主脊勾勒作笔骨；山脚经水线雾化隐没（mask 渐隐），与水面融为一体
          （draw 动效见 theme.css qingshan 区块） */}
      <svg className="qingshan-peak" viewBox="0 0 340 220" aria-hidden="true">
        <defs>
          <linearGradient id="qs-ink-near" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="rgba(52, 116, 96, .46)" />
            <stop offset=".55" stopColor="rgba(52, 116, 96, .14)" />
            <stop offset="1" stopColor="rgba(52, 116, 96, 0)" />
          </linearGradient>
          <linearGradient id="qs-ink-mid" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="rgba(64, 118, 100, .26)" />
            <stop offset="1" stopColor="rgba(64, 118, 100, 0)" />
          </linearGradient>
          <linearGradient id="qs-ink-far" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="rgba(74, 128, 110, .2)" />
            <stop offset="1" stopColor="rgba(74, 128, 110, 0)" />
          </linearGradient>
          <filter id="qs-blur-near" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="1.4" />
          </filter>
          <filter id="qs-blur-mid" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="2.2" />
          </filter>
          <filter id="qs-blur-far" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="3" />
          </filter>
          <filter id="qs-blur-dot" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation=".8" />
          </filter>
        </defs>
        <g className="qs-float">
          {/* 远山：满幅连绵淡影，雾化最重 */}
          <path
            className="qs-mtn qs-mtn--far"
            d="M0 220 L 0 132 C 20 118, 38 96, 56 78 C 64 70, 72 70, 80 78 C 96 94, 112 108, 130 114 C 144 118, 156 108, 166 96 C 168 93, 169 91, 170 91 C 171 91, 172 93, 174 96 C 184 108, 196 118, 210 114 C 228 108, 244 94, 260 78 C 268 70, 276 70, 284 78 C 302 96, 320 118, 340 132 L 340 220 Z"
            fill="url(#qs-ink-far)"
            filter="url(#qs-blur-far)"
          />
          {/* 中景：两侧低山连绵，中间调过渡（中段横鞍隐入主山之后） */}
          <path
            className="qs-mtn qs-mtn--mid"
            d="M0 220 L 0 150 C 14 132, 28 108, 44 92 C 52 84, 60 84, 68 92 C 82 108, 96 120, 114 126 C 140 134, 200 134, 226 126 C 244 120, 258 108, 272 92 C 280 84, 288 84, 296 92 C 312 108, 326 132, 340 150 L 340 220 Z"
            fill="url(#qs-ink-mid)"
            filter="url(#qs-blur-mid)"
          />
          {/* 主山：对称三峰，坡面带碎笔起伏，墨色随山脚渐淡入水 */}
          <path
            className="qs-mtn qs-mtn--near"
            d="M0 220 C 10 198, 18 178, 30 160 C 40 144, 46 130, 54 116 C 62 102, 70 88, 78 74 C 82 66, 88 62, 92 66 C 98 74, 108 84, 118 90 C 124 93, 130 92, 136 86 C 144 78, 152 60, 158 46 C 162 36, 166 30, 170 28 C 174 30, 178 36, 182 46 C 188 60, 196 78, 204 86 C 210 92, 216 93, 222 90 C 232 84, 242 74, 248 66 C 252 62, 258 66, 262 74 C 270 88, 278 102, 286 116 C 294 130, 300 144, 310 160 C 322 178, 330 198, 340 220 Z"
            fill="url(#qs-ink-near)"
            filter="url(#qs-blur-near)"
          />
          {/* 点苔：峰头与坡脊的浓墨提点（对称布点） */}
          <g className="qs-dots" fill="rgba(47, 102, 84, .4)" filter="url(#qs-blur-dot)">
            <circle cx="170" cy="34" r="2.2" />
            <circle cx="162" cy="44" r="1.5" />
            <circle cx="178" cy="44" r="1.5" />
            <circle cx="90" cy="72" r="1.8" />
            <circle cx="250" cy="72" r="1.8" />
            <circle cx="62" cy="126" r="2" />
            <circle cx="278" cy="126" r="2" />
            <circle cx="122" cy="104" r="1.4" />
            <circle cx="218" cy="104" r="1.4" />
          </g>
          {/* 笔骨：主脊勾勒 + 对称皴线 */}
          <path
            className="qs-ridge qs-ridge--front"
            pathLength={1}
            d="M0 220 C 10 198, 18 178, 30 160 C 40 144, 46 130, 54 116 C 62 102, 70 88, 78 74 C 82 66, 88 62, 92 66 C 98 74, 108 84, 118 90 C 124 93, 130 92, 136 86 C 144 78, 152 60, 158 46 C 162 36, 166 30, 170 28 C 174 30, 178 36, 182 46 C 188 60, 196 78, 204 86 C 210 92, 216 93, 222 90 C 232 84, 242 74, 248 66 C 252 62, 258 66, 262 74 C 270 88, 278 102, 286 116 C 294 130, 300 144, 310 160 C 322 178, 330 198, 340 220 Z"
          />
          <path className="qs-ridge qs-ridge--accent" pathLength={1} d="M96 76 C 102 84, 110 90, 120 92" />
          <path className="qs-ridge qs-ridge--accent" pathLength={1} d="M244 76 C 238 84, 230 90, 220 92" />
          <path className="qs-ridge qs-ridge--accent" pathLength={1} d="M160 48 C 152 62, 142 76, 132 86" />
          <path className="qs-ridge qs-ridge--accent" pathLength={1} d="M180 48 C 188 62, 198 76, 208 86" />
        </g>
      </svg>

      {/* 青山知识库入口（可交互）：题字 + 裂隙与山形同一套升沉物理——置于山形之上、水体之下（z1），
          初始整体沉在水下被水体遮没，split 后随山体同一节奏升起、鼠标离开一同沉回；
          裂隙正压水线与山同宽，题字落位山脚留白处；点击后开屏整体由下向上推移进入第三页面 */}
      <div
        className="qingshan-steps"
        role="button"
        tabIndex={0}
        aria-label="进入青山知识库"
        onClick={(e) => {
          e.stopPropagation()
          enter('qingshan')
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            enter('qingshan')
          }
        }}
      >
        {/* 破水涟漪：山体刺破水线瞬间自基线扩出一圈椭圆环（一次性，随浮出触发） */}
        <span className="qingshan-pierce qingshan-pierce--1" aria-hidden="true" />
        <span className="qingshan-pierce qingshan-pierce--2" aria-hidden="true" />
        <div className="qingshan-label">青山知识库</div>
        <div className="qingshan-line" aria-hidden="true" />
      </div>

      {/* 实体门（可交互） */}
      <div className="doors-group main-doors">
        {renderDoor('tower', towerSlotRef)}
        {renderDoor('cockpit', cockpitSlotRef)}
      </div>

      {/* 水面：深青水质底色 + 涌浪波带 + 波光细纹 + 光斑晕染 + 粼粼光点 + 水线浪脊 +
          近岸浪花 + 中心/偏心涟漪 + 山体升沉的水中呼应（沉底辉光/上浮气泡/破水涟漪，
          见 theme.css qs-sunken-glow / qs-bubbles / water-breach 区块） */}
      <div className="water-surface" aria-hidden="true">
        <div className="qs-sunken-glow" />
        <div className="water-swell water-swell--1" />
        <div className="water-swell water-swell--2" />
        <div className="water-shimmer" />
        <div className="water-gleam" />
        <div className="water-sparkle water-sparkle--1" />
        <div className="water-sparkle water-sparkle--2" />
        <div className="water-sparkle water-sparkle--3" />
        <div className="water-crest" />
        <div className="water-foam" />
        <span className="water-ripple water-ripple--1" />
        <span className="water-ripple water-ripple--2" />
        <span className="water-ripple water-ripple--3" />
        <span className="water-ripple water-ripple--4" />
        <span className="water-ripple water-ripple--5" />
        <span className="water-ripple water-ripple--6" />
        {/* 山体潜伏水下时气泡缓缓上冒；split 上浮时换上浮组连串爆发 */}
        <div className="qs-bubbles qs-bubbles--sink" aria-hidden="true">
          <span /><span /><span />
        </div>
        <div className="qs-bubbles qs-bubbles--rise" aria-hidden="true">
          <span /><span /><span /><span /><span />
        </div>
        {/* 破水涟漪：山体上浮/下沉扰动水面，自破水点沿水面向外扩出的椭圆环 */}
        <span className="water-breach water-breach--1" aria-hidden="true" />
        <span className="water-breach water-breach--2" aria-hidden="true" />
        <span className="water-breach water-breach--3" aria-hidden="true" />
      </div>
    </div>
  )
}
