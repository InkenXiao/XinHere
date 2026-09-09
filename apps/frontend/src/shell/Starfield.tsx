// 夜态星空粒子 canvas（参照瞭望塔 Starfield）：星点缓慢漂移 + 闪烁 + 偶发流星；
// 昼态/看板页淡出停帧省电；prefers-reduced-motion 直接不渲染
import { useEffect, useRef } from 'react'

export default function Starfield({ visible }: { visible: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const visibleRef = useRef(visible)
  const loopRef = useRef<((now: number) => void) | null>(null)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    visibleRef.current = visible
    // 淡出停帧后恢复起帧（循环在完全淡出后自停）
    if (visible && rafRef.current === null && loopRef.current) {
      rafRef.current = requestAnimationFrame(loopRef.current)
    }
  }, [visible])

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const DPR = Math.min(2, window.devicePixelRatio || 1)
    let stars: {
      x: number; y: number; r: number; base: number; tw: number; ph: number; vx: number; warm: boolean
    }[] = []
    let meteors: { x: number; y: number; vx: number; vy: number; life: number }[] = []
    let nextMeteor = 0
    let lastT = 0
    let alpha = visibleRef.current ? 1 : 0

    const resize = () => {
      const w = innerWidth
      const h = innerHeight
      canvas.width = Math.round(w * DPR)
      canvas.height = Math.round(h * DPR)
      canvas.style.width = `${w}px`
      canvas.style.height = `${h}px`
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0)
      stars = []
      const n = Math.max(90, Math.min(220, Math.round(w / 8)))
      for (let i = 0; i < n; i++) {
        const big = Math.random() < 0.06
        stars.push({
          x: Math.random() * w,
          y: h * 0.52 * (0.03 + 0.97 * Math.random()),
          r: big ? 1.5 + Math.random() * 0.9 : 0.4 + Math.random() * 1.1,
          base: big ? 0.75 + Math.random() * 0.25 : 0.25 + Math.random() * 0.6,
          tw: 0.4 + Math.random() * 1.6,
          ph: Math.random() * 6.283,
          vx: (2.5 + Math.random() * 7) * (Math.random() < 0.94 ? 1 : -1),
          warm: Math.random() < 0.14,
        })
      }
    }
    resize()
    window.addEventListener('resize', resize)

    const loop = (now: number) => {
      const vis = visibleRef.current
      alpha += ((vis ? 1 : 0) - alpha) * 0.04
      const w = innerWidth
      const h = innerHeight
      ctx.clearRect(0, 0, w, h)
      if (alpha < 0.01) {
        // 完全淡出：停帧省电，待 visible 翻真再恢复
        rafRef.current = null
        return
      }
      rafRef.current = requestAnimationFrame(loop)
      const dt = Math.min(0.05, Math.max(0.001, (now - lastT) / 1000))
      lastT = now
      for (const s of stars) {
        s.x += s.vx * dt
        if (s.x > w + 8) s.x = -8
        else if (s.x < -8) s.x = w + 8
        const twk = 0.72 + 0.28 * Math.sin(now * 0.0022 * s.tw + s.ph)
        const a = s.base * twk * alpha
        ctx.beginPath()
        ctx.arc(s.x, s.y, s.r, 0, 6.283)
        ctx.fillStyle = s.warm ? `rgba(232,208,170,${a})` : `rgba(220,238,252,${a})`
        ctx.fill()
      }
      if (now > nextMeteor && meteors.length < 2) {
        nextMeteor = now + 3500 + Math.random() * 6000
        meteors.push({ x: w * (0.15 + Math.random() * 0.7), y: h * (0.02 + Math.random() * 0.2), vx: -(220 + Math.random() * 160), vy: 120 + Math.random() * 80, life: 1 })
      }
      meteors = meteors.filter((m) => m.life > 0)
      for (const m of meteors) {
        m.x += m.vx * dt
        m.y += m.vy * dt
        m.life -= dt * 0.55
        const grad = ctx.createLinearGradient(m.x, m.y, m.x - m.vx * 0.22, m.y - m.vy * 0.22)
        grad.addColorStop(0, `rgba(235,248,255,${0.85 * Math.max(0, m.life) * alpha})`)
        grad.addColorStop(1, 'rgba(235,248,255,0)')
        ctx.strokeStyle = grad
        ctx.lineWidth = 1.2
        ctx.beginPath()
        ctx.moveTo(m.x, m.y)
        ctx.lineTo(m.x - m.vx * 0.22, m.y - m.vy * 0.22)
        ctx.stroke()
      }
    }
    loopRef.current = loop
    rafRef.current = requestAnimationFrame(loop)
    return () => {
      window.removeEventListener('resize', resize)
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      rafRef.current = null
      loopRef.current = null
    }
  }, [])

  return <canvas ref={ref} className="tw-fx" aria-hidden="true" />
}
