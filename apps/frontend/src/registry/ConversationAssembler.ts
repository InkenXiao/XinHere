// 会话装配器（docs/design/04 §4）：事件流 → 渲染节点
// 不变量：chunk 流式尾部隔离；tool/call↔tool/result 由后端 refs 配对（前端零配对逻辑）；
// 未知事件 type 且 ignorable=false → fail-closed 抛错；回放与实时共用同一 feed 路径
import type { PlatformEvent } from '@/types'
import type { Usage } from '@contracts/events'
import type { ComponentBase, ComponentDefinition, Matched } from './types'

export type Node =
  | { key: string; type: 'user'; seq: number; content: string }
  | { key: string; type: 'inject'; seq: number; content: string }
  | { key: string; type: 'assistant'; seq: number; content: string; usage?: Usage }
  | { key: string; type: 'steps'; seq: number; turn: number; items: { step: number; status: 'active' | 'done' }[] }
  | {
      key: string
      type: 'tool'
      seq: number
      call_id: string
      name: string
      args: Record<string, unknown>
      status: 'running' | 'done' | 'error'
      result?: string
    }
  | { key: string; type: 'component'; seq: number; base: ComponentBase; state: unknown; def: ComponentDefinition }
  | { key: string; type: 'error'; seq: number; code: string; message: string }

export interface AssemblerSnapshot {
  nodes: Node[]
  streaming: { text: string } | null
  running: boolean
  turn: number
}

export const FRAMEWORK_TYPES = new Set([
  'turn/start',
  'turn/end',
  'step/start',
  'step/end',
  'user/message',
  'assistant/chunk',
  'assistant/message',
  'tool/call',
  'tool/result',
  'component/request',
  'component/submit',
  'component/submit-ack',
  'baseline',
  'projection',
  'error',
])

export class ConversationAssembler {
  private defs: ComponentDefinition[]
  private nodes: Node[] = []
  private compIdx = new Map<string, number>() // `${kind}:${id}` → nodes 下标
  private streaming: { text: string } | null = null
  private maxSeq = 0
  private running = false
  private turn = 0
  private stepsIdx = -1
  projections: Record<string, unknown> = {}

  constructor(defs: ComponentDefinition[]) {
    this.defs = defs
  }

  reset() {
    this.nodes = []
    this.compIdx.clear()
    this.streaming = null
    this.maxSeq = 0
    this.running = false
    this.turn = 0
    this.stepsIdx = -1
    this.projections = {}
  }

  snapshot(): AssemblerSnapshot {
    return {
      nodes: [...this.nodes],
      streaming: this.streaming ? { ...this.streaming } : null,
      running: this.running,
      turn: this.turn,
    }
  }

  getMaxSeq() {
    return this.maxSeq
  }

  findComponent(componentId: string): Extract<Node, { type: 'component' }> | null {
    for (const n of this.nodes) {
      if (n.type === 'component' && n.base.component_id === componentId) return n
    }
    return null
  }

  feedAll(events: PlatformEvent[]) {
    for (const e of events) this.feed(e)
  }

  feed(evt: PlatformEvent) {
    // 乱序/迟到帧丢弃（baseline 无 seq 除外）
    if (evt.type !== 'baseline') {
      if (evt.seq >= 0 && evt.seq <= this.maxSeq) return
      if (evt.seq > 0) this.maxSeq = evt.seq
    }
    const d = evt.data
    switch (evt.type) {
      case 'turn/start':
        this.turn = (d.turn as number) ?? this.turn + 1
        this.running = true
        return
      case 'turn/end': {
        this.running = false
        this.flushStream(evt.seq)
        this.markStepsDone()
        return
      }
      case 'step/start':
        this.pushStep(evt.seq, (d.step as number) ?? 0)
        return
      case 'step/end':
        this.endStep((d.step as number) ?? 0)
        return
      case 'user/message': {
        this.flushStream(evt.seq)
        const content = String(d.content ?? '')
        if (d.source === 'inject') {
          this.nodes.push({ key: `inject:${evt.seq}`, type: 'inject', seq: evt.seq, content })
        } else {
          this.nodes.push({ key: `user:${evt.seq}`, type: 'user', seq: evt.seq, content })
        }
        return
      }
      case 'assistant/chunk': {
        // 流式尾部隔离：不进 nodes，单独渲染路径
        if (!this.streaming) this.streaming = { text: '' }
        this.streaming.text += String(d.delta ?? '')
        return
      }
      case 'assistant/message': {
        this.streaming = null
        this.nodes.push({
          key: `assistant:${evt.seq}`,
          type: 'assistant',
          seq: evt.seq,
          content: String(d.content ?? ''),
          usage: d.usage as Usage | undefined,
        })
        return
      }
      case 'tool/call':
        this.nodes.push({
          key: `tool:${evt.seq}`,
          type: 'tool',
          seq: evt.seq,
          call_id: String(d.call_id ?? ''),
          name: String(d.name ?? ''),
          args: (d.arguments as Record<string, unknown>) ?? {},
          status: 'running',
        })
        return
      case 'tool/result': {
        // refs 由后端配对：[call_seq]，前端按 seq 直接落位
        const refs = (d.refs as number[]) ?? []
        const target = refs.length
          ? this.nodes.find((n) => n.type === 'tool' && n.seq === refs[0])
          : this.nodes.find((n) => n.type === 'tool' && n.call_id === d.call_id)
        if (target && target.type === 'tool') {
          target.status = d.is_error ? 'error' : 'done'
          target.result = String(d.content ?? '')
          if (d.outcome === 'unknown') target.result = '（中断恢复：结果未知）'
        } else {
          // 配对缺失也保留痕迹
          this.nodes.push({
            key: `tool:${evt.seq}`,
            type: 'tool',
            seq: evt.seq,
            call_id: String(d.call_id ?? ''),
            name: String(d.name ?? ''),
            args: {},
            status: d.is_error ? 'error' : 'done',
            result: String(d.content ?? ''),
          })
        }
        return
      }
      case 'component/request':
        this.startComponent(evt)
        return
      case 'component/submit': {
        const cid = String(d.component_id ?? '')
        const node = this.findComponent(cid)
        if (node) {
          node.base = { ...node.base, status: d.action === 'cancelled' ? 'cancelled' : 'submitted' }
          // 让 definition 以 end 角色收尾（可提取 summary 等）
          node.state = node.def.reduce(node.state, evt, { id: cid, role: 'end' })
        }
        return
      }
      case 'component/submit-ack':
        return
      case 'baseline': {
        const pending = (d.pending ?? {}) as {
          interrupts?: { component_id: string; kind: string; props: Record<string, unknown>; interrupt_id: string }[]
        }
        for (const it of pending.interrupts ?? []) {
          if (this.findComponent(it.component_id)) continue
          const def = this.defs.find((x) => x.kind === it.kind)
          if (!def) throw new Error(`未注册组件 ${it.kind}（baseline 恢复失败）`)
          this.createComponentNode(evt, def, {
            component_id: it.component_id,
            kind: it.kind,
            kind_version: def.version,
            interrupt_id: it.interrupt_id,
            props: it.props ?? {},
            status: 'open',
          })
        }
        this.projections = { ...this.projections, ...((d.projections as Record<string, unknown>) ?? {}) }
        return
      }
      case 'projection': {
        const k = d.key as string
        if (k) this.projections[k] = d.value
        return
      }
      case 'error':
        this.nodes.push({
          key: `error:${evt.seq}`,
          type: 'error',
          seq: evt.seq,
          code: String(d.code ?? 'INTERNAL'),
          message: String(d.message ?? '未知错误'),
        })
        this.running = false
        return
      default:
        return this.feedBusiness(evt)
    }
  }

  /** 业务事件族折叠：逐 definition 匹配；未命中按 ignorable 规则 fail-closed */
  private feedBusiness(evt: PlatformEvent) {
    for (const def of this.defs) {
      const m = def.match(evt)
      if (!m) continue
      this.applyMatched(def, evt, m)
      return
    }
    if (evt.ignorable === true) return
    throw new Error(`未知事件类型 ${evt.type}（seq=${evt.seq}），fail-closed`)
  }

  private applyMatched(def: ComponentDefinition, evt: PlatformEvent, m: Matched) {
    const key = `${def.kind}:${m.id}`
    const idx = this.compIdx.get(key)
    if (idx !== undefined) {
      if (m.role === 'start') {
        // 同一 (kind,id) 第二个 start：开发态抛错，生产态记错跳过
        const msg = `组件不变量冲突：${key} 重复 start（seq=${evt.seq}）`
        if (import.meta.env.DEV) throw new Error(msg)
        console.error(msg)
        return
      }
      const node = this.nodes[idx] as Extract<Node, { type: 'component' }>
      node.state = def.reduce(node.state, evt, m)
      if (m.role === 'end') node.base = { ...node.base, status: 'submitted' }
      return
    }
    // 未建基座：视为该组件的 start（事件族可直接起组件，如 kanban-card）
    const base: ComponentBase = {
      component_id: m.id,
      kind: def.kind,
      kind_version: def.version,
      props: {},
      status: m.role === 'end' ? 'submitted' : 'open',
    }
    const node = this.createComponentNode(evt, def, base)
    node.state = def.reduce(node.state, evt, m)
  }

  private startComponent(evt: PlatformEvent) {
    const d = evt.data
    const kind = String(d.kind ?? '')
    const kindVersion = (d.kind_version as number) ?? 1
    const def = this.defs.find((x) => x.kind === kind && x.version === kindVersion)
    if (!def) throw new Error(`未注册组件 ${kind}@${kindVersion}（seq=${evt.seq}）`)
    const componentId = String(d.component_id ?? '')
    if (this.findComponent(componentId)) {
      const msg = `组件不变量冲突：${kind}:${componentId} 重复 start（seq=${evt.seq}）`
      if (import.meta.env.DEV) throw new Error(msg)
      console.error(msg)
      return
    }
    const base: ComponentBase = {
      component_id: componentId,
      kind,
      kind_version: kindVersion,
      interrupt_id: d.interrupt_id as string | undefined,
      props: (d.props as Record<string, unknown>) ?? {},
      status: 'open',
    }
    this.createComponentNode(evt, def, base)
  }

  private createComponentNode(
    evt: PlatformEvent,
    def: ComponentDefinition,
    base: ComponentBase,
  ): Extract<Node, { type: 'component' }> {
    const node: Extract<Node, { type: 'component' }> = {
      key: `component:${base.kind}:${base.component_id}:${evt.seq}`,
      type: 'component',
      seq: evt.seq,
      base,
      state: def.init ? def.init(base) : undefined,
      def,
    }
    this.nodes.push(node)
    this.compIdx.set(`${def.kind}:${base.component_id}`, this.nodes.length - 1)
    // 事件族别名：props 中的业务 id（form_id/batch_id 等）也指向本节点
    for (const alias of def.aliases?.(base.props) ?? []) {
      if (!this.compIdx.has(`${def.kind}:${alias}`)) {
        this.compIdx.set(`${def.kind}:${alias}`, this.nodes.length - 1)
      }
    }
    return node
  }

  private flushStream(seq: number) {
    if (this.streaming && this.streaming.text) {
      this.nodes.push({ key: `assistant:${seq}:flush`, type: 'assistant', seq, content: this.streaming.text })
    }
    this.streaming = null
  }

  private pushStep(seq: number, step: number) {
    let node = this.stepsIdx >= 0 ? (this.nodes[this.stepsIdx] as Extract<Node, { type: 'steps' }>) : null
    if (!node || node.type !== 'steps' || node.turn !== this.turn) {
      this.markStepsDone()
      this.nodes.push({ key: `steps:${this.turn}:${seq}`, type: 'steps', seq, turn: this.turn, items: [] })
      this.stepsIdx = this.nodes.length - 1
      node = this.nodes[this.stepsIdx] as Extract<Node, { type: 'steps' }>
    }
    if (!node.items.some((s) => s.step === step)) {
      node.items.push({ step, status: 'active' })
    } else {
      node.items = node.items.map((s) => (s.step === step ? { ...s, status: 'active' } : s))
    }
  }

  private endStep(step: number) {
    const node = this.stepsIdx >= 0 ? (this.nodes[this.stepsIdx] as Extract<Node, { type: 'steps' }>) : null
    if (node && node.type === 'steps') {
      node.items = node.items.map((s) => (s.step === step ? { ...s, status: 'done' } : s))
    }
  }

  private markStepsDone() {
    const node = this.stepsIdx >= 0 ? (this.nodes[this.stepsIdx] as Extract<Node, { type: 'steps' }>) : null
    if (node && node.type === 'steps') {
      node.items = node.items.map((s) => ({ ...s, status: 'done' as const }))
    }
  }
}
