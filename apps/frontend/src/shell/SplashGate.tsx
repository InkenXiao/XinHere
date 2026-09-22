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

      {/* 实体门（可交互） */}
      <div className="doors-group main-doors">
        {renderDoor('tower', towerSlotRef)}
        {renderDoor('cockpit', cockpitSlotRef)}
      </div>

      {/* 水面：深青水质底色 + 涌浪波带 + 波光细纹 + 光斑晕染 + 粼粼光点 + 水线浪脊 +
          近岸浪花 + 中心/偏心涟漪（纯展示，不拦截交互） */}
      <div className="water-surface" aria-hidden="true">
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
      </div>

      {/* 青山知识库入口：双门展开后自水中浮出（位于两门正下方），极简题字悬浮于一条发光「能量裂隙」之上；
          点击后开屏整体由下向上推移进入第三页面 */}
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
        <div className="qingshan-label">青山知识库</div>
        <div className="qingshan-line" aria-hidden="true" />
      </div>
    </div>
  )
}
