// 开屏双门「背景窗口」：两张全屏背景图固定加载于开屏页，仅透过左右卡片内部可见（窗口机制见 theme.css 开屏区块）
// 默认交叉合拢，鼠标进入展开、离开收合；点击左门「向前放大铺满全屏」进入 Xin语，右门「向后缩小消失」进入 Xin台
// 双门展开（split）后，水线以下浮现青山倒影与液态水面（threejs-components liquid1：鼠标划过起涟漪、
// 「青山」中心定时荡开大环），水面中央「青山」题字自水下浮出；点击题字开屏整体由下向上推移，进入第三页面
// （样式见 theme.css door-water 区块）
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
  // 液态水面画布 + 「青山」入口 ref（定时涟漪以「青山」二字中心为落点）
  const liquidCanvasRef = useRef<HTMLCanvasElement>(null)
  const markRef = useRef<HTMLButtonElement>(null)

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

  // 液态水面（threejs-components liquid1，public/assets/liquid1.min.js，demo 离线版同款）：
  // 画布铺满水线以下区域，底图 = 青山倒影（垂直翻转 + 渐隐）+ 水色烘进纹理，波纹以「高度场
  // 扭曲采样 UV」显形——程序波自体荡漾 + 指针涟漪 + 「青山」中心定时大环，完全不经光照；
  // 库以 ES Module 运行时动态 import，加载失败 / 不支持 WebGL / 偏好减弱动效时静默回退，
  // CSS 倒影（.qs-reflection）照常工作
  useEffect(() => {
    const wrap = liquidCanvasRef.current?.parentElement
    const canvas = liquidCanvasRef.current
    if (!wrap || !canvas) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    let disposed = false
    const cleanups: Array<() => void> = []
    const timers: number[] = []
    const later = (fn: () => void, ms: number) => {
      const id = window.setTimeout(() => {
        if (!disposed) fn()
      }, ms)
      timers.push(id)
    }

    void (async () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const w = wrap.clientWidth || window.innerWidth
      const h = wrap.clientHeight || Math.round(window.innerHeight * 0.34)

      // 底图：青山倒影图，取景与 CSS 兜底层一致（100% auto / center 70%）
      const img = new Image()
      img.src = '/assets/qingshan-reflect.jpg'
      await new Promise<void>((res) => {
        if (img.complete && img.naturalWidth) return res()
        img.onload = () => res()
        img.onerror = () => res()
        window.setTimeout(res, 4000)
      })
      if (disposed || !img.naturalWidth) return

      const drawW = w
      const drawH = (w * img.naturalHeight) / img.naturalWidth
      const offY = (h - drawH) * 0.7

      // 1) 倒影层：图片按取景画入临时画布
      const refl = document.createElement('canvas')
      refl.width = Math.round(w * dpr)
      refl.height = Math.round(h * dpr)
      const rc = refl.getContext('2d')
      if (!rc) return
      rc.scale(dpr, dpr)
      rc.drawImage(img, 0, offY, drawW, drawH)

      // 2) 最终纹理：垂直翻转的倒影叠在青灰水面上（整幅不透明，配色 = 墙色在水线处的延续）
      const off = document.createElement('canvas')
      off.width = refl.width
      off.height = refl.height
      const ctx = off.getContext('2d')
      if (!ctx) return
      ctx.scale(dpr, dpr)
      let g = ctx.createLinearGradient(0, 0, 0, h)
      g.addColorStop(0, '#f6f5f0')
      g.addColorStop(1, '#f4f3ee')
      ctx.fillStyle = g
      ctx.fillRect(0, 0, w, h)
      g = ctx.createLinearGradient(0, 0, 0, h)
      g.addColorStop(0, 'rgba(20,20,16,0)')
      g.addColorStop(0.34, 'rgba(20,20,16,.018)')
      g.addColorStop(1, 'rgba(20,20,16,.048)')
      ctx.fillStyle = g
      ctx.fillRect(0, 0, w, h)
      // 倒影 = 垂直翻转后烘入渐隐（方向与 CSS mask 一致：水线浓、向下淡出）
      const fadeCv = document.createElement('canvas')
      fadeCv.width = refl.width
      fadeCv.height = refl.height
      const fc = fadeCv.getContext('2d')
      if (!fc) return
      fc.scale(dpr, dpr)
      fc.save()
      fc.translate(0, h)
      fc.scale(1, -1)
      fc.drawImage(refl, 0, 0, w, h)
      fc.restore()
      fc.globalCompositeOperation = 'destination-out'
      const fade = fc.createLinearGradient(0, 0, 0, h)
      fade.addColorStop(0, 'rgba(0,0,0,.03)')
      fade.addColorStop(0.38, 'rgba(0,0,0,.26)')
      fade.addColorStop(0.76, 'rgba(0,0,0,.60)')
      fade.addColorStop(0.96, 'rgba(0,0,0,.94)')
      fc.fillStyle = fade
      fc.fillRect(0, 0, w, h)
      // 左右浓度均衡：图源左侧近山浓墨、右侧远山淡彩，左缘再减淡一层（50% 处过渡到 0）
      const fadeH = fc.createLinearGradient(0, 0, w, 0)
      fadeH.addColorStop(0, 'rgba(0,0,0,.93)')
      fadeH.addColorStop(0.5, 'rgba(0,0,0,0)')
      fc.fillStyle = fadeH
      fc.fillRect(0, 0, w, h)
      // 倒影整体降不透明度（统一减淡，不用白洗渐变——色相保持原样）
      ctx.globalAlpha = 0.26
      ctx.drawImage(fadeCv, 0, 0, w, h)
      ctx.globalAlpha = 1

      // 液态渲染：库加载 / WebGL 失败则什么都不做，CSS 倒影照常工作
      const libUrl = '/assets/liquid1.min.js'
      const mod: { default: (canvas: HTMLCanvasElement) => any } = await import(
        /* @vite-ignore */ libUrl
      )
      const app = mod.default(canvas)
      cleanups.push(() => {
        try {
          app.dispose?.()
        } catch {
          /* 忽略销毁异常 */
        }
      })
      await app.loadImage(off.toDataURL('image/png'))
      const lmat = app.liquidPlane.material
      // 清澈水面 = 纯折射方案：场景无灯，光照全部归零（无白翳无黑斑），
      // 倒影纹理经 emissive 通道直出原色，波纹以「高度场扭曲采样 UV」显形；
      // 重写库的 shader 补丁：去掉其 RGB 色散偏移（彩边=脏）与法线光照（白翳来源）
      lmat.metalness = 0
      lmat.roughness = 1
      lmat.envMapIntensity = 0
      lmat.emissive.setRGB(1, 1, 1)
      lmat.emissiveMap = lmat.map
      lmat.emissiveIntensity = 1
      lmat.needsUpdate = true
      app.liquidPlane.uniforms.displacementScale.value = 1.6
      app.setRain(false)
      lmat.onBeforeCompile = (shader: { uniforms: Record<string, { value: unknown }>; fragmentShader: string }) => {
        Object.assign(shader.uniforms, app.liquidPlane.uniforms)
        shader.uniforms.uRes = { value: { x: 1, y: 1 } } // 画布分辨率（three 只读 .x/.y）
        shader.uniforms.uTime = { value: 0 } // 程序波时钟（rAF 驱动，见下方 tick）
        lmat.__us = shader.uniforms.uRes
        lmat.__ut = shader.uniforms.uTime
        shader.fragmentShader =
          'uniform vec2 uvMapScale;\nuniform sampler2D displacementMap;\nuniform float displacementScale;\nuniform vec2 uRes;\nuniform float uTime;\n' +
          shader.fragmentShader
        shader.fragmentShader = shader.fragmentShader.replace('#include <emissivemap_fragment>', `
          #ifdef USE_EMISSIVEMAP
            vec4 disp = texture2D(displacementMap, vUv);
            vec2 waterN = vec3(disp.b, disp.a, sqrt(1.0 - dot(disp.ba, disp.ba))).xy;
            /* 屏幕坐标直采：画布像素 = 纹理像素 1:1，绕开平面/相机映射误差 */
            vec2 texUv = gl_FragCoord.xy / uRes;
            /* 程序波：三组不同方向/频率/速度的行波叠加，整片水面自体持续荡漾，无需任何触发 */
            float asp = uRes.x / max(uRes.y, 1.0);
            vec2 q = vec2(texUv.x * asp, texUv.y) * 0.72;
            float wA = sin(q.y * 9.0  + uTime * 1.1);
            float wB = sin(q.x * 6.0 + q.y * 4.0 - uTime * 0.8);
            float wC = sin(q.x * 13.0 - q.y * 7.0 + uTime * 1.6);
            float wh = wA * .45 + wB * .35 + wC * .25;
            vec2 wg = vec2(
                6.0 * .35 * cos(q.x*6.0 + q.y*4.0 - uTime*0.8)
              + 13.0 * .25 * cos(q.x*13.0 - q.y*7.0 + uTime*1.6),
                9.0 * .45 * cos(q.y*9.0 + uTime*1.1)
              +  4.0 * .35 * cos(q.x*6.0 + q.y*4.0 - uTime*0.8)
              -  7.0 * .25 * cos(q.x*13.0 - q.y*7.0 + uTime*1.6)
            );
            vec2 waveUv = vec2(wg.x * asp, wg.y) * 0.00075;
            /* 合成采样偏移：程序波（自动荡漾）+ 涟漪高度场（指针/落滴） */
            vec2 ripUv = texUv + waterN * displacementScale * 0.02 + waveUv;
            totalEmissiveRadiance *= texture2D(emissiveMap, ripUv).rgb;
            /* 波光压到 3%：只留极轻的明暗呼吸 */
            totalEmissiveRadiance *= 1.0 + wh * 0.03;
          #endif
        `)
      }

      // 指针涟漪：大半径低力度的平缓水波（像流水推开，而不是砸出水坑），节流防淹没高度场
      let lastDrop = 0
      const onMove = (e: PointerEvent) => {
        const r = wrap.getBoundingClientRect()
        if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) return
        const now = performance.now()
        if (now - lastDrop < 60) return
        lastDrop = now
        const nx = ((e.clientX - r.left) / r.width) * 2 - 1
        const ny = -(((e.clientY - r.top) / r.height) * 2 - 1)
        app.liquidPlane.addDrop(nx, ny, 0.13, 0.05)
      }
      document.addEventListener('pointermove', onMove)
      cleanups.push(() => document.removeEventListener('pointermove', onMove))

      // 自动落滴（点睛）：每 2s 以「青山」二字中心为落点荡开一圈大涟漪（大环慢波）；
      // 水面未显示（opacity≈0）时不落滴，涟漪不留到门打开之后
      const autoRain = () => {
        const mark = markRef.current
        if (!mark) return
        if (parseFloat(getComputedStyle(wrap).opacity) < 0.5) return
        const r = wrap.getBoundingClientRect()
        const m = mark.getBoundingClientRect()
        const nx = ((m.left + m.width / 2 - r.left) / r.width) * 2 - 1
        const ny = -(((m.top + m.height / 2 - r.top) / r.height) * 2 - 1)
        app.liquidPlane.addDrop(nx, ny, 0.2, 0.12)
      }
      const rainId = window.setInterval(autoRain, 2000)
      cleanups.push(() => window.clearInterval(rainId))

      // 关键修复：库的 resize() 初始化时可能拿不到容器尺寸（画布缓冲停在默认 300×150，
      // 被 CSS 拉伸后整面糊化），init 后主动补尺寸并挂窗口 resize 兜底
      const fixSize = () => {
        try {
          app.three.resize()
        } catch {
          /* 忽略 */
        }
        if (lmat.__us) {
          lmat.__us.value.x = canvas.width
          lmat.__us.value.y = canvas.height
        }
      }
      fixSize()
      later(fixSize, 300)
      later(fixSize, 1200)
      let rsTimer = 0
      const onResize = () => {
        window.clearTimeout(rsTimer)
        rsTimer = window.setTimeout(fixSize, 150)
      }
      window.addEventListener('resize', onResize)
      cleanups.push(() => {
        window.removeEventListener('resize', onResize)
        window.clearTimeout(rsTimer)
      })

      // 环境波时钟：独立 rAF 推进 uTime（秒）
      let raf = 0
      const tickTime = () => {
        if (lmat.__ut) lmat.__ut.value = performance.now() / 1000
        raf = requestAnimationFrame(tickTime)
      }
      tickTime()
      cleanups.push(() => cancelAnimationFrame(raf))
    })().catch((err) => {
      console.warn('[liquid] init failed, fallback to CSS reflection', err)
    })

    return () => {
      disposed = true
      timers.forEach((id) => window.clearTimeout(id))
      cleanups.forEach((fn) => fn())
    }
  }, [])

  const enter = (door: Door) => {
    if (entering) return
    // 青山知识库：跳过门卡形变测量，setMode 触发第三层（内嵌知识库平台首页）自下向上推进，
    // 开屏整体同步上移退场将其露出；KB_URL 未配置时第三层仅呈现青绿山水加载幕
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

      {/* 实体门（可交互） */}
      <div className="doors-group main-doors">
        {renderDoor('tower', towerSlotRef)}
        {renderDoor('cockpit', cockpitSlotRef)}
      </div>

      {/* 水面倒影（CSS 兜底层）：青山画卷垂直翻转沉入水线以下，沿水线全宽铺开、向下渐隐、
          随水波轻晃；WebGL 液态水面可用时与其同位叠加、被液态层盖过，不可用时独立承担倒影 */}
      <div className="qs-reflection" aria-hidden="true">
        <i />
      </div>

      {/* 液态水面：threejs-components liquid1 画布铺满水线以下区域，底图 = 青山倒影 + 水色烘进纹理，
          波纹实时扭曲倒影；鼠标划过水面起涟漪，「青山」中心每 2s 荡开一圈大涟漪；
          加载失败 / 不支持 WebGL / 偏好减弱动效时静默回退到 CSS 倒影（初始化见上方 useEffect） */}
      <div className="water-liquid" aria-hidden="true">
        <canvas ref={liquidCanvasRef} />
      </div>

      {/* 青山知识库入口（可交互）：宋体「青山」+ 细线箭头，身后垫一卷水墨远山；
          双门展开后自水下浮出、鼠标离开沉回，点击后开屏整体由下向上推移进入第三页面 */}
      <button
        ref={markRef}
        type="button"
        className="qingshan-mark"
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
        <span className="qs-text">青山</span>
      </button>
    </div>
  )
}
