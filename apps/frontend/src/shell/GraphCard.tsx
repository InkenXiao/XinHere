// 业务图谱卡 (P3-4): 经 /xuanpu/ontology/graph 拉取本体子图, 纯 SVG 力导向渲染
// 点击节点 → 以该节点为中心重新展开邻域; 邻域节点上限 100
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/transport/api'

interface GNode { name: string; type?: string; desc?: string }
interface GEdge { src: string; tgt: string; type?: string }
interface GraphData { nodes: GNode[]; edges: GEdge[]; note?: string }

interface P { x: number; y: number; vx: number; vy: number; node: GNode; deg: number }

const W = 520
const H = 300

function layout(nodes: GNode[], edges: GEdge[]): P[] {
  const pts: P[] = nodes.map((node, i) => ({
    node, deg: 0, vx: 0, vy: 0,
    x: W / 2 + 170 * Math.cos((i / Math.max(1, nodes.length)) * 2 * Math.PI - Math.PI / 2),
    y: H / 2 + 96 * Math.sin((i / Math.max(1, nodes.length)) * 2 * Math.PI - Math.PI / 2),
  }))
  const idx = new Map(nodes.map((n, i) => [n.name, i]))
  for (const e of edges) {
    const ai = idx.get(e.src), bi = idx.get(e.tgt)
    if (ai === undefined || bi === undefined) continue
    pts[ai].deg += 1; pts[bi].deg += 1
  }
  for (let it = 0; it < 140; it++) {
    for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) {
      const a = pts[i], b = pts[j]
      const dx = a.x - b.x, dy = a.y - b.y
      const d2 = dx * dx + dy * dy || 1
      const f = 24000 / d2
      const d = Math.sqrt(d2)
      a.vx += (dx / d) * f; a.vy += (dy / d) * f
      b.vx -= (dx / d) * f; b.vy -= (dy / d) * f
    }
    for (const e of edges) {
      const ai = idx.get(e.src), bi = idx.get(e.tgt)
      if (ai === undefined || bi === undefined) continue
      const a = pts[ai], b = pts[bi]
      const dx = b.x - a.x, dy = b.y - a.y
      const d = Math.sqrt(dx * dx + dy * dy) || 1
      const k = (d - 130) * 0.02
      a.vx += (dx / d) * k; a.vy += (dy / d) * k
      b.vx -= (dx / d) * k; b.vy -= (dy / d) * k
    }
    for (const p of pts) {
      p.vx += (W / 2 - p.x) * 0.01; p.vy += (H / 2 - p.y) * 0.01
      p.vx *= 0.8; p.vy *= 0.8
      p.x = Math.max(34, Math.min(W - 34, p.x + p.vx))
      p.y = Math.max(20, Math.min(H - 20, p.y + p.vy))
    }
  }
  return pts
}

export default function GraphCard({ cardKey }: { cardKey: string }) {
  const [data, setData] = useState<GraphData | null>(null)
  const [center, setCenter] = useState('')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const reqId = useRef(0)

  const load = useCallback(async (c: string) => {
    const id = ++reqId.current
    setLoading(true); setErr('')
    try {
      const r = await api<GraphData>('GET', `/xuanpu/ontology/graph?center=${encodeURIComponent(c)}&limit=60`)
      if (id === reqId.current) setData(r)
    } catch (e) {
      if (id === reqId.current) setErr(e instanceof Error ? e.message : String(e))
    } finally {
      if (id === reqId.current) setLoading(false)
    }
  }, [])

  useEffect(() => { void load('') }, [load])

  const pts = useMemo(() => (data?.nodes?.length ? layout(data.nodes, data.edges) : []), [data])
  const idx = useMemo(() => new Map(pts.map((p, i) => [p.node.name, i])), [pts])
  const maxDeg = useMemo(() => Math.max(1, ...pts.map((p) => p.deg)), [pts])

  return (
    <article className="cp-card kind-graph" data-card={cardKey}>
      <h3>业务图谱{center ? <span className="gc-center"> · {center}</span> : ''}</h3>
      <p className="cp-sub">{loading ? '图谱加载中…' : err ? `加载失败: ${err}` : `${data?.nodes?.length ?? 0} 个业务对象 · 点击节点展开关系`}</p>
      <div className="gc-canvas">
        {pts.length > 0 && (
          <svg viewBox={`0 0 ${W} ${H}`}>
            {(data?.edges ?? []).map((e, i) => {
              const ai = idx.get(e.src), bi = idx.get(e.tgt)
              if (ai === undefined || bi === undefined) return null
              const a = pts[ai], b = pts[bi]
              return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                stroke="rgba(23,132,188,.35)" strokeWidth={1.2} />
            })}
            {pts.map((p) => {
              const r = 9 + 11 * (p.deg / maxDeg)
              return (
                <g key={p.node.name} className="gc-node" onClick={() => { setCenter(p.node.name); void load(p.node.name) }}>
                  <circle cx={p.x} cy={p.y} r={r} />
                  <text x={p.x} y={p.y + r + 11}>{p.node.name.slice(0, 8)}</text>
                </g>
              )
            })}
          </svg>
        )}
      </div>
      {center && (
        <div className="meta">
          <span className="go" onClick={() => { setCenter(''); void load('') }}>← 返回全景</span>
        </div>
      )}
    </article>
  )
}
