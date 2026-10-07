// 执行过程折叠组（参照 XuanPu 对话页「五段式」效果）：
// - 运行中：脉冲点 + 「执行中 · N 步」，默认展开过程可见
// - 结束态：「已完成 · N 步」/ 中断「已中断 · N 步」自动展开
// - 组内为竖轨时间轴：模型调用行 + 工具轨迹项（耗时徽章 + 入参/结果 JSON）
import { useState } from 'react'
import type { Node } from '@/registry/ConversationAssembler'
import { toolZh } from '@/utils'

export type TraceState = 'running' | 'done' | 'interrupted' | 'error'

const JSON_MAX = 2000 // 入参/结果展示截断阈值（防撑爆气泡）

function fmtJson(obj: unknown): string {
  let txt: string
  try {
    txt = typeof obj === 'string' ? obj : JSON.stringify(obj ?? '', null, 2)
  } catch {
    txt = String(obj)
  }
  return txt.length > JSON_MAX ? `${txt.slice(0, JSON_MAX)}\n…(已截断)` : txt
}

/** 模型调用轨迹项：✦ 模型调用 · 第 N 轮 —— 耗时 · 生成字数 */
function ModelItem({ node }: { node: Extract<Node, { type: 'model' }> }) {
  const badge =
    node.status === 'running'
      ? '生成中…'
      : `${node.durationMs != null ? `${node.durationMs}ms` : '完成'}${node.chars != null ? ` · ${node.chars} 字` : ''}`
  return (
    <div className="trace-item" key={node.key}>
      <div className="trace-rail">
        <span className="t-ico">✦</span>
        <span className="t-line" />
      </div>
      <div className="trace-main">
        <h6>
          <span className="trace-name">模型调用 · 第 {node.round} 轮</span>
          <span className={`chat-trace__badge ${node.status === 'running' ? 'chat-trace__badge--run' : 'chat-trace__badge--ok'}`}>
            {badge}
          </span>
        </h6>
      </div>
    </div>
  )
}

/** 工具轨迹项：🔧 工具调用 · 中文名 —— 执行中 / 完成 · Xms / 失败；入参与结果 JSON 展示 */
function ToolItem({ node }: { node: Extract<Node, { type: 'tool' }> }) {
  const [openArgs, setOpenArgs] = useState(true)
  const argsTxt = fmtJson(node.args)
  const resultTxt = node.result ? fmtJson(node.result) : ''
  const badge =
    node.status === 'running'
      ? '执行中'
      : node.status === 'error'
        ? `失败${node.durationMs != null ? ` · ${node.durationMs}ms` : ''}`
        : `完成${node.durationMs != null ? ` · ${node.durationMs}ms` : ''}`
  return (
    <div className={`trace-item ${node.status === 'running' ? 'trace-item--running' : ''} ${node.status === 'error' ? 'trace-item--err' : ''}`} key={node.key}>
      <div className="trace-rail">
        <span className="t-ico">🔧</span>
        <span className="t-line" />
      </div>
      <div className="trace-main">
        <h6>
          <span className="trace-name">工具调用 · {toolZh(node.name)}</span>
          <span
            className={`chat-trace__badge ${
              node.status === 'running' ? 'chat-trace__badge--run' : node.status === 'error' ? 'chat-trace__badge--err' : 'chat-trace__badge--ok'
            }`}
          >
            {badge}
          </span>
        </h6>
        <div className="t-desc">
          {argsTxt !== '{}' && (
            <>
              <div className="t-json-label t-json-toggle" onClick={() => setOpenArgs((v) => !v)}>
                入参 {openArgs ? '▾' : '▸'}
              </div>
              {openArgs && <pre className="t-json">{argsTxt}</pre>}
            </>
          )}
          {resultTxt && (
            <>
              <div className="t-json-label">结果</div>
              <pre className="t-json">{resultTxt}</pre>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/** 执行过程折叠组：items 为该 turn 内按事件顺序的 model/tool 轨迹项 */
export default function TraceGroup({ items, state }: { items: Node[]; state: TraceState }) {
  const [open, setOpen] = useState(state !== 'done') // 结束默认收起，中断/错误展开
  const steps = items.length
  const badgeText =
    state === 'running'
      ? steps > 0
        ? `执行中 · ${steps} 步`
        : '进行中'
      : `${state === 'done' ? '已完成' : '已中断'} · ${steps} 步`
  const headIcon = state === 'running' ? <span className="chat-pulse" /> : <span className={`chat-done-ico ${state === 'error' ? 'err' : state === 'interrupted' ? 'warn' : ''}`}>{state === 'error' ? '!' : state === 'interrupted' ? '⏸' : '✓'}</span>

  return (
    <div className={`chat-trace chat-process ${open ? 'open' : ''} trace-${state}`}>
      <div className="chat-trace__head" onClick={() => setOpen((v) => !v)}>
        {headIcon}
        <span className="chat-trace__name">执行过程</span>
        <span className={`chat-trace__badge ${state === 'running' ? 'chat-trace__badge--run' : state === 'done' ? 'chat-trace__badge--ok' : state === 'error' ? 'chat-trace__badge--err' : 'chat-trace__badge--warn'}`}>
          {badgeText}
        </span>
        <span className="chat-trace__chev">▾</span>
      </div>
      <div className="chat-trace__body chat-process__body">
        {items.map((n) => {
          if (n.type === 'model') return <ModelItem key={n.key} node={n} />
          if (n.type === 'tool') return <ToolItem key={n.key} node={n} />
          return null
        })}
      </div>
    </div>
  )
}
