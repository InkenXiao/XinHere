// 文件在线预览弹层（对齐 scms-frontend FilePreviewDialog / 问数 workbench）：
// docx → docx-preview 渲染；doc → 按文件头识别真实格式（PK=docx，否则 CFB 提取纯文本）；
// Excel → xlsx 解析成分片表格；pdf/图片/txt → blob URL 原生渲染；
// ppt/pptx/doc/xls → 后端 /preview-pdf 端点 soffice 转 PDF（缓存）后 iframe 原生渲染。
// （旧方案微软 view.officeapps.live.com 在线查看器已废弃：其云端拉不到鉴权内网文件，必然失败。）
// 由于 KB 文件走我方后端代理（需平台 token），所有拉取都带 authHeaders，而非裸 fetch。
import { useEffect, useRef, useState } from 'react'
import { Spin, Tabs, Table, Empty } from 'antd'
import { KbFileError, fetchKbBlob } from './kbFile'
// docx-preview / xlsx 按需动态加载（仅点预览时拉取，避免并入主包拖累首屏）

export interface KbFileRef {
  url: string
  fileName: string
}

type PreviewKind = 'pdf' | 'excel' | 'word' | 'legacy-word' | 'office-pdf' | 'image' | 'text' | 'unsupported'
type ExcelSheet = { name: string; rows: string[][] }

const IMAGE_EXTS = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg']

function extOf(url: string, fileName: string): string {
  const src = (fileName || url).split(/[?#]/)[0]
  const m = src.split('.').pop()
  return (m || '').toLowerCase()
}

function kindOf(ext: string): PreviewKind {
  if (ext === 'pdf') return 'pdf'
  if (ext === 'xls' || ext === 'xlsx') return 'excel'
  if (ext === 'docx') return 'word'
  if (ext === 'doc') return 'legacy-word'
  // ppt/pptx 走后端 soffice 转 PDF（老 .ppt 前端无渲染库；.pptx 渲染库兼容性也不如转 PDF）
  if (ext === 'ppt' || ext === 'pptx') return 'office-pdf'
  if (IMAGE_EXTS.includes(ext)) return 'image'
  if (ext === 'txt' || ext === 'csv') return 'text'
  return 'unsupported'
}

/** 后端 PDF 化预览端点：office 文件转 PDF（带缓存）；非 office / 转换失败时后端原样透传原始文件 */
function pdfPreviewUrl(url: string): string {
  return url.replace(/\/preview$/, '/preview-pdf')
}

// ---------- 旧版 .doc（二进制 OLE）纯文本提取，对齐 scms-frontend ----------
function decodeLegacy(bytes: Uint8Array, encoding: string) {
  try {
    return new TextDecoder(encoding).decode(bytes)
  } catch {
    return ''
  }
}
function normLegacy(value: string) {
  return value
    .replace(/\r/g, '\n')
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
function legacyScore(value: string) {
  if (!value) return -Infinity
  const readable = (value.match(/[\u4E00-\u9FFF\w\s，。！？、；：,.!?;:()（）\-]/g) || []).length
  const invalid = (value.match(/[�]/g) || []).length
  return readable - invalid * 10
}
function decodeCompressed(bytes: Uint8Array) {
  return ['gb18030', 'windows-1252']
    .map((enc) => decodeLegacy(bytes, enc))
    .sort((a, b) => legacyScore(b) - legacyScore(a))[0] || ''
}
function cleanLegacy(value: string) {
  let text = value
    .replace(/[\x02\x05\x08]/g, '')
    .replace(/\x07/g, '\t')
    .replace(/[\x0A-\x0D]/g, '\n')
    .replace(/\x1E/g, '‑')
  let prev = ''
  while (prev !== text) {
    prev = text
    text = text.replace(/\x13[^\x13\x14\x15]*\x14?([^\x13\x14\x15]*)\x15/g, '$1')
  }
  return normLegacy(text)
}
function extractLegacyWord(arrayBuffer: ArrayBuffer, XLSX: typeof import('xlsx')): string {
  const container = XLSX.CFB.read(new Uint8Array(arrayBuffer), { type: 'array' })
  const wordDoc = XLSX.CFB.find(container, 'WordDocument')
  if (!wordDoc?.content) return ''
  const docBytes = new Uint8Array(wordDoc.content)
  if (docBytes.length < 0x1a6) return ''
  const view = new DataView(docBytes.buffer, docBytes.byteOffset, docBytes.byteLength)
  const flags = view.getUint16(0x0a, true)
  const tableStream = XLSX.CFB.find(container, flags & 0x0200 ? '1Table' : '0Table')
  if (!tableStream?.content) return ''
  const tableBytes = new Uint8Array(tableStream.content)
  const tableView = new DataView(tableBytes.buffer, tableBytes.byteOffset, tableBytes.byteLength)
  let pos = view.getUint32(0x1a2, true)
  if (pos >= tableBytes.length) return ''
  while (tableBytes[pos] === 1) {
    if (pos + 3 > tableBytes.length) return ''
    pos += 3 + tableView.getUint16(pos + 1, true)
  }
  if (tableBytes[pos] !== 2 || pos + 5 > tableBytes.length) return ''
  const pieceCount = (tableView.getUint32(pos + 1, true) - 4) / 12
  if (!Number.isInteger(pieceCount) || pieceCount <= 0 || pieceCount > 100000) return ''
  const cpOffset = pos + 5
  const pdOffset = cpOffset + (pieceCount + 1) * 4
  if (pdOffset + pieceCount * 8 > tableBytes.length) return ''
  const pieces: string[] = []
  for (let i = 0; i < pieceCount; i += 1) {
    const startCp = tableView.getUint32(cpOffset + i * 4, true)
    const endCp = tableView.getUint32(cpOffset + (i + 1) * 4, true)
    if (endCp <= startCp) continue
    const off = pdOffset + i * 8
    const raw = tableView.getUint32(off + 2, true)
    const isUnicode = (raw & 0x40000000) === 0
    const filePos = isUnicode ? raw : Math.floor(((raw & 0xbfffffff) >>> 0) / 2)
    const byteLen = (endCp - startCp) * (isUnicode ? 2 : 1)
    if (filePos + byteLen > docBytes.length) continue
    const pieceBytes = docBytes.slice(filePos, filePos + byteLen)
    pieces.push(isUnicode ? decodeLegacy(pieceBytes, 'utf-16le') : decodeCompressed(pieceBytes))
  }
  return cleanLegacy(pieces.join(''))
}

export function FilePreviewDialog({ file, onClose }: { file: KbFileRef | null; onClose: () => void }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [kind, setKind] = useState<PreviewKind>('unsupported')
  const [objectUrl, setObjectUrl] = useState('')
  const [excelSheets, setExcelSheets] = useState<ExcelSheet[]>([])
  const [activeSheet, setActiveSheet] = useState('')
  const [legacyText, setLegacyText] = useState('')
  const wordRef = useRef<HTMLDivElement>(null)

  useEffect(() => () => { if (objectUrl) URL.revokeObjectURL(objectUrl) }, [objectUrl])

  useEffect(() => {
    if (!file) return
    let cancelled = false
    setLoading(true)
    setError('')
    setLegacyText('')
    setExcelSheets([])
    setActiveSheet('')
    const ext = extOf(file.url, file.fileName)
    const k = kindOf(ext)
    setKind(k)

    const load = async () => {
      try {
        if (k === 'word' || k === 'legacy-word') {
          const { renderAsync } = await import('docx-preview')
          const blob = await fetchKbBlob(file.url)
          const buf = await blob.arrayBuffer()
          const header = new Uint8Array(await blob.slice(0, 4).arrayBuffer())
          const isDocx = k === 'word' || (header[0] === 0x50 && header[1] === 0x4b)
          if (isDocx) {
            if (cancelled) return
            let container = wordRef.current
            if (k === 'legacy-word') {
              // 容器分支只在 kind==='word' 时渲染：切换后需等一帧 React 提交，ref 才会挂上
              setKind('word')
              await new Promise((resolve) => requestAnimationFrame(resolve))
              if (cancelled) return
              container = wordRef.current
            }
            if (!container) throw new Error('Word 预览容器未就绪')
            container.innerHTML = ''
            await renderAsync(blob, container, undefined, {
              breakPages: true,
              ignoreWidth: false,
              ignoreHeight: false,
              useBase64URL: true,
            })
          } else {
            if (cancelled) return
            const XLSX = await import('xlsx')
            setLegacyText(extractLegacyWord(buf, XLSX))
          }
        } else if (k === 'excel') {
          const XLSX = await import('xlsx')
          const blob = await fetchKbBlob(file.url)
          const wb = XLSX.read(await blob.arrayBuffer(), { type: 'array', cellDates: true })
          const sheets = wb.SheetNames.map((name) => ({
            name,
            rows: (XLSX.utils.sheet_to_json(wb.Sheets[name], {
              header: 1,
              raw: false,
              defval: '',
            }) as unknown[][])
              .slice(0, 1001)
              .map((row) => row.slice(0, 50).map((cell) => String(cell ?? ''))),
          }))
          if (cancelled) return
          setExcelSheets(sheets)
          setActiveSheet(sheets[0]?.name || '')
        } else if (k === 'pdf' || k === 'text' || k === 'image') {
          const blob = await fetchKbBlob(file.url)
          if (cancelled) return
          setObjectUrl(URL.createObjectURL(blob))
        } else if (k === 'office-pdf') {
          // 后端 soffice 转 PDF（按 file_id 缓存）；端点对非 office/转换失败原样透传原始文件。
          // iframe 直接指向后端端点（携带平台 token 的同源 Cookie/session 由 request 层写入的
          // Authorization 头无法用于 iframe，故走 blob：带 authHeaders 拉回再渲染）。
          const blob = await fetchKbBlob(pdfPreviewUrl(file.url))
          if (cancelled) return
          const ctype = blob.type.toLowerCase()
          if (ctype.includes('application/pdf')) {
            setObjectUrl(URL.createObjectURL(blob))
          } else if (ctype.startsWith('image/')) {
            // 后端透传了原始文件且恰好是图片（理论不会走到：图片走 image 分支）
            setKind('image')
            setObjectUrl(URL.createObjectURL(blob))
          } else {
            setError('该文件暂不支持在线预览，请下载后查看')
          }
        } else {
          if (!cancelled) setError('暂不支持该文件类型的在线预览，请下载后查看')
        }
      } catch (e) {
        console.error('[FilePreviewDialog] 文件加载失败：', e)
        if (!cancelled) {
          // KbFileError = 网关/HTTP 业务错误（如 DOWNLOAD 行为权限不足，message 即原文案）；
          // 其余（网络/渲染异常）保持通用文案
          setError(e instanceof KbFileError ? e.message : '文件加载失败，请确认文件地址有效且当前账号有访问权限')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => { cancelled = true }
  }, [file])

  if (!file) return null

  const title = file.fileName || '文件预览'
  const contentHeight = 'h-[min(70vh,720px)]'
  const activeRows = excelSheets.find((s) => s.name === activeSheet)?.rows ?? []
  const columns = (activeRows[0] ?? []).map((label, i) => ({
    title: label || `列${i + 1}`,
    dataIndex: `c${i}`,
    key: `c${i}`,
    minWidth: 120,
    ellipsis: true,
  }))
  const tableRows = activeRows.slice(1, 1001).map((row, r) => ({
    key: `r${r}`,
    ...Object.fromEntries(columns.map((c, i) => [c.key, row[i] || ''])),
  }))

  return (
    <div
      className="fixed inset-0 z-[80] flex items-start justify-center overflow-auto bg-black/60 p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="mt-[5vh] w-[min(1200px,92vw)] rounded-2xl border border-[var(--glass-bd)] bg-[var(--pop-bg)] shadow-2xl">
        <div className="flex items-center justify-between border-b border-[var(--glass-bd)] px-5 py-3">
          <b className="text-base text-[var(--ink-100)]">{title}</b>
          <button type="button" className="cursor-pointer text-xl leading-none text-[var(--muted)] hover:text-[var(--text)]" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="flex min-h-[min(70vh,720px)] items-center justify-center overflow-hidden bg-[#f5f7fa] p-4">
          {/* Word 容器必须在 loading 结束前就挂载：renderAsync 往 wordRef.current 写内容，
              而 loading 期间 ref 还是 null（此前容器只在 loading 结束后才渲染，两者互等，
              Word 预览因此从未成功）→ 容器常驻，loading 期间隐藏并叠加居中 Spin */}
          {error ? (
            <Empty description={error} />
          ) : kind === 'word' ? (
            <div className="w-full">
              <div
                ref={wordRef}
                className={`word-preview-document w-full overflow-auto bg-white [&_.docx-wrapper]:bg-white [&_section.docx]:bg-white [&_section.docx]:p-8 ${loading ? 'hidden' : ''}`}
              />
              {loading && (
                <div className="flex min-h-[min(70vh,720px)] items-center justify-center">
                  <Spin />
                </div>
              )}
            </div>
          ) : loading ? (
            <Spin />
          ) : kind === 'pdf' || kind === 'text' ? (
            <iframe src={objectUrl} className={`w-full ${contentHeight} border-0`} title={title} />
          ) : kind === 'image' ? (
            <img src={objectUrl} alt={title} className="max-h-[min(70vh,720px)] max-w-full object-contain" />
          ) : kind === 'legacy-word' ? (
            <pre className="w-full whitespace-pre-wrap rounded bg-white p-6 text-base text-[var(--ink-100)]">{legacyText}</pre>
          ) : kind === 'excel' ? (
            excelSheets.length ? (
              <div className="w-full">
                <Tabs
                  activeKey={activeSheet}
                  onChange={setActiveSheet}
                  items={excelSheets.map((s) => ({ key: s.name, label: s.name }))}
                />
                <Table
                  size="small"
                  bordered
                  scroll={{ x: 'max-content', y: 520 }}
                  columns={columns}
                  dataSource={tableRows}
                  pagination={false}
                />
              </div>
            ) : (
              <Empty description="Excel 文件中没有可展示的数据" />
            )
          ) : kind === 'office-pdf' ? (
            <iframe src={objectUrl} className={`w-full ${contentHeight} border-0`} title={title} />
          ) : (
            <Empty description="暂不支持该文件类型的在线预览，请下载后查看" />
          )}
        </div>
      </div>
    </div>
  )
}
