// 结构化溯源卡（```sources 围栏 JSON → 组件）：
// 知识库文档（文件名 + 预览/下载，走我方后端 KB 代理，带平台 token fetch blob）+
// 命中片段（文件 → 片段二级面板，cite_index 与正文 [n] 角标联动定位高亮）+
// 联网来源（标题链接，新页面打开）+ 路由/联网状态标签 + 超过 5 条折叠展开。
// 对齐问数 workbench 参考实现 ChatMessage.vue 的「参考来源」区块（同一上游接口）。
import { useEffect, useId, useRef, useState } from 'react'
import { useUiStore } from '@/scms/uiShim'
import { KbFileError, fetchKbBlob } from './kbFile'

// 对齐 contracts 生成的 SourcesPayload（字段可选）：来源可能来自 tool/progress.sources（契约类型）
// 或 ```sources 围栏 JSON.parse（运行时任意形状），渲染处一律兜底过滤。
export interface CitationChunkMeta {
  file_id?: number | string
  file_name?: string
  chunk_index?: number
  cite_index?: number // 正文角标 [n] 的 n，点击角标据此定位高亮本片段
  chunk_text?: string
  score?: number
}

export interface SourcePayload {
  citations?: { file_id?: number | string; file_name?: string; preview_url?: string; download_url?: string }[]
  web_results?: { title?: string; url?: string; snippet?: string }[]
  citation_chunks?: CitationChunkMeta[]
  route?: string
  web_ok?: boolean
}

const MAX_PREVIEW_SOURCES = 5

/** 解析 ```sources 围栏内容；三类来源全缺返回 null（调用方降级不渲染） */
export function parseSourcesPayload(code: string): SourcePayload | null {
  try {
    const d = JSON.parse(code) as SourcePayload
    if (!Array.isArray(d.citations) && !Array.isArray(d.web_results) && !Array.isArray(d.citation_chunks)) {
      return null
    }
    return d
  } catch {
    return null
  }
}

/** 拉取 KB 文件流并触发浏览器保存；预览走 FilePreviewDialog（弹层内渲染） */
export function downloadKbFile(url: string, filename: string) {
  const toast = (t: string, k?: 'info' | 'err') => useUiStore.getState().toast(t, k)
  const doDownload = async () => {
    let blob: Blob
    try {
      blob = await fetchKbBlob(url)
    } catch (e) {
      // KbFileError = 网关/HTTP 业务错误（权限不足等，message 即原文案）；其余为网络异常
      toast(e instanceof KbFileError ? e.message : '文件服务网络异常，请稍后重试', 'err')
      return
    }
    const objectUrl = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = objectUrl
    a.download = filename || 'file'
    document.body.appendChild(a)
    a.click()
    a.remove()
    // 延迟释放 objectURL
    setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
  }
  void doDownload()
}

export function SourceList({ data }: { data: SourcePayload }) {
  const [expanded, setExpanded] = useState(false)
  // 整卡折叠：默认收起（点标题行展开/收起），减少对正文的遮挡
  const [collapsed, setCollapsed] = useState(true)
  // 「文件 → 命中片段」二级面板：展开的文件集合 + 正在高亮的 cite_index（角标定位用）
  const [openFiles, setOpenFiles] = useState<Set<string>>(new Set())
  const [hlCite, setHlCite] = useState<number | null>(null)
  const uid = useId()
  const kb = (data.citations ?? []).filter((c) => c.file_id != null)
  const web = (data.web_results ?? []).filter((w) => !!w.url)
  const chunks = (data.citation_chunks ?? []).filter((c) => c.file_id != null)
  const total = kb.length + web.length
  // citeFocus 订阅须在 early return 之前（hooks 顺序一致性）
  // chunks 按 file_id 分组挂到对应文档行；citation 行缺失时按文件名生成只读分组兜底
  const chunksByFile = new Map<string, CitationChunkMeta[]>()
  for (const c of chunks) {
    const k = String(c.file_id)
    const arr = chunksByFile.get(k)
    if (arr) arr.push(c)
    else chunksByFile.set(k, [c])
  }
  const ghostGroups =
    kb.length === 0
      ? [...new Map(chunks.map((c) => [String(c.file_id), c.file_name ?? '未命名文档'])).entries()].map(([fid, fname]) => ({
          fid,
          fname,
        }))
      : []

  // 正文角标 [n] 点击 → uiStore.citeFocus → 展开所属文件分组 + 滚动高亮命中片段（2.4s 后褪色）
  const citeFocus = useUiStore((s) => s.citeFocus)
  // chunks 每次渲染都是新数组，不能进依赖（否则 hlCite 定时器引起的重渲染会反复触发定位，把用户滚走的位置拉回来）
  const chunksRef = useRef(chunks)
  chunksRef.current = chunks
  useEffect(() => {
    if (!citeFocus) return
    const hit = chunksRef.current.find((c) => c.cite_index === citeFocus.citeIndex)
    if (!hit) return
    // 消费即清：未清理时 citeFocus 常驻，任何重渲染都会再次定位
    useUiStore.getState().clearCiteFocus()
    const fid = String(hit.file_id)
    setOpenFiles((s) => (s.has(fid) ? s : new Set(s).add(fid)))
    setHlCite(citeFocus.citeIndex)
    // 双 rAF：等 setOpenFiles 触发的重渲染把片段元素挂出来后再定位
    let raf2 = 0
    const raf = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => {
        document.getElementById(`${uid}-cite-${fid}-${hit.chunk_index}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      })
    })
    const timer = setTimeout(() => setHlCite(null), 2400)
    return () => {
      cancelAnimationFrame(raf)
      cancelAnimationFrame(raf2)
      clearTimeout(timer)
    }
    // 重复点击同一角标时 requestCiteFocus 会写入新 ts（新对象），effect 自然重跑
  }, [citeFocus, uid])

  const toggleFile = (fid: string) =>
    setOpenFiles((s) => {
      const n = new Set(s)
      if (n.has(fid)) n.delete(fid)
      else n.add(fid)
      return n
    })

  if (total === 0 && chunks.length === 0) return null
  const overLimit = total > MAX_PREVIEW_SOURCES
  const visKb = expanded || kb.length <= MAX_PREVIEW_SOURCES ? kb : kb.slice(0, MAX_PREVIEW_SOURCES)
  const visWeb = expanded || web.length <= MAX_PREVIEW_SOURCES ? web : web.slice(0, Math.max(0, MAX_PREVIEW_SOURCES - kb.length))
  const fused = (data.route ?? '').includes('web')

  // 未分层 `button{color:inherit;border:0}` 压过 Tailwind 分层 utilities，颜色/边框类需带 `!`
  const linkBtn =
    'border-none! bg-transparent p-0! text-xs! font-medium text-[var(--accent)]! hover:underline'

  return (
    <div className="mt-3 rounded-xl border border-[var(--card-border)] bg-[var(--tag-bg)] px-3 py-2.5">
      <div className="flex items-center gap-2">
        <button
          type="button"
          data-testid="source-card-toggle"
          onClick={() => setCollapsed((v) => !v)}
          className="flex items-center gap-1.5 border-none! bg-transparent p-0! text-xs! font-semibold text-[var(--ink-100)]! transition-colors hover:text-[var(--accent)]!"
          title={collapsed ? '展开来源' : '收起来源'}
        >
          <span aria-hidden>📎</span>
          <span>参考来源（{total}）</span>
          <span aria-hidden className="text-xs font-normal">{collapsed ? '▸' : '▾'}</span>
        </button>
        {!collapsed && fused && (
          <span className="rounded-full border border-[var(--accent)]/50 px-2 py-0.5 text-xs text-[var(--accent)]">
            已结合联网结果
          </span>
        )}
        {!collapsed && data.web_ok === false && (
          <span className="text-xs text-[var(--ink-30)]">联网服务暂不可用，已尽力用本地资料回答</span>
        )}
      </div>
      {!collapsed && (<>
      {visKb.length > 0 && (
        <div className="mt-2">
          <div className="text-xs font-medium text-[var(--ink-30)]">知识库文档</div>
          <div className="mt-1 flex flex-col gap-1">
            {visKb.map((c) => {
              const fid = String(c.file_id)
              const fchunks = chunksByFile.get(fid) ?? []
              const open = openFiles.has(fid)
              return (
                <div key={c.file_id}>
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="rounded-md bg-[var(--accent)]/10 px-2 py-0.5 text-xs text-[var(--ink-100)]">
                      {c.file_name ?? '未命名文档'}
                    </span>
                    {fchunks.length > 0 && (
                      <button type="button" className={linkBtn} onClick={() => toggleFile(fid)}>
                        {open ? '收起片段' : `命中片段 ${fchunks.length}`}
                      </button>
                    )}
                    {c.preview_url && (
                      <button
                        type="button"
                        className={linkBtn}
                        onClick={() => useUiStore.getState().openKbFilePreview({ url: c.preview_url!, fileName: c.file_name ?? '未命名文档' })}
                      >
                        预览
                      </button>
                    )}
                    {/* <button type="button" className={linkBtn} onClick={() => downloadKbFile(c.download_url, c.file_name)}>
                      下载
                    </button> */}
                  </div>
                  {open && fchunks.length > 0 && (
                    <div className="mt-1 flex flex-col gap-1">
                      {fchunks.map((ch) => (
                        <div
                          key={`${fid}:${ch.chunk_index}`}
                          id={`${uid}-cite-${fid}-${ch.chunk_index}`}
                          className={`rounded-lg border px-2 py-1.5 text-xs transition-colors duration-300 ${
                            hlCite === ch.cite_index
                              ? 'border-[var(--accent)]! bg-[var(--accent)]/10'
                              : 'border-[var(--card-border)]'
                          }`}
                        >
                          <div className="mb-0.5 flex items-center gap-1.5">
                            <span className="rounded bg-[var(--accent)]/15 px-1.5 py-0.5 font-semibold text-[var(--accent)]">
                              [{ch.cite_index ?? '·'}]
                            </span>
                            {typeof ch.score === 'number' && ch.score > 0 && (
                              <span className="text-[var(--ink-30)]">相关度 {ch.score.toFixed(2)}</span>
                            )}
                          </div>
                          <p className="m-0 whitespace-pre-wrap text-[var(--ink-50)]">{ch.chunk_text ?? ''}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
      {/* 无 citation 行但有片段：按文件名生成只读分组（防御上游只回 citation_chunks） */}
      {kb.length === 0 &&
        ghostGroups.map(({ fid, fname }) => {
          const fchunks = chunksByFile.get(fid) ?? []
          const open = openFiles.has(fid)
          return (
            <div key={`ghost:${fid}`} className="mt-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="rounded-md bg-[var(--accent)]/10 px-2 py-0.5 text-xs text-[var(--ink-100)]">
                  {fname}
                </span>
                {fchunks.length > 0 && (
                  <button type="button" className={linkBtn} onClick={() => toggleFile(fid)}>
                    {open ? '收起片段' : `命中片段 ${fchunks.length}`}
                  </button>
                )}
              </div>
              {open && fchunks.length > 0 && (
                <div className="mt-1 flex flex-col gap-1">
                  {fchunks.map((ch) => (
                    <div
                      key={`${fid}:${ch.chunk_index}`}
                      id={`${uid}-cite-${fid}-${ch.chunk_index}`}
                      className={`rounded-lg border px-2 py-1.5 text-xs transition-colors duration-300 ${
                        hlCite === ch.cite_index
                          ? 'border-[var(--accent)]! bg-[var(--accent)]/10'
                          : 'border-[var(--card-border)]'
                      }`}
                    >
                      <div className="mb-0.5 flex items-center gap-1.5">
                        <span className="rounded bg-[var(--accent)]/15 px-1.5 py-0.5 font-semibold text-[var(--accent)]">
                          [{ch.cite_index ?? '·'}]
                        </span>
                        {typeof ch.score === 'number' && ch.score > 0 && (
                          <span className="text-[var(--ink-30)]">相关度 {ch.score.toFixed(2)}</span>
                        )}
                      </div>
                      <p className="m-0 whitespace-pre-wrap text-[var(--ink-50)]">{ch.chunk_text ?? ''}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      {visWeb.length > 0 && (
        <div className="mt-2">
          <div className="text-xs font-medium text-[var(--ink-30)]">联网来源</div>
          <div className="mt-1 flex flex-col gap-1">
            {visWeb.map((w) => (
              <a
                key={w.url}
                href={w.url}
                target="_blank"
                rel="noopener noreferrer"
                className="truncate text-sm text-[var(--accent)]! hover:underline"
                title={w.title ?? w.url}
              >
                {w.title ?? w.url}
              </a>
            ))}
          </div>
        </div>
      )}
      {overLimit && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-2 border-none! bg-transparent p-0! text-xs! font-medium text-[var(--ink-50)]! hover:text-[var(--accent)]!"
        >
          {expanded ? '收起来源' : `展开全部来源（共 ${total} 条）`}
        </button>
      )}
      </>)}
    </div>
  )
}
