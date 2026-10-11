// mermaid 围栏识别与容错清洗（移植自 xin-knowledge mermaidFencePlugin，纯字符串处理）：
// LLM 直出的 mermaid 常带语法瑕疵（节点语句挤一行、end 关键字冲突、边标签/节点文本未加引号、
// 头部粘连 mermaidflowchart 等），sanitizeMermaid 做无侵入修复后再交给 mermaid 渲染。

/** mermaid 已知图型首行（用于无语言标注围栏的嗅探） */
const MERMAID_HEAD =
  /^(?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|journey|gantt|pie|mindmap|timeline|gitGraph)\b/

export const isMermaidSource = (code: string) => {
  const first = code
    .replace(/^\uFEFF/, '')
    .split('\n')
    .map((line) => line.trim())
    .find((line) => line && !line.startsWith('%%'))
  return !!first && MERMAID_HEAD.test(first)
}

export const isMermaidFenceInfo = (info: string) =>
  info.trim().split(/\s+/)[0].toLowerCase().startsWith('mermaid')

/** 无标注围栏的额外保守门槛：需含 --> 或显式 flowchart/graph 方向头，避免误伤普通代码块 */
export const MERMAID_FLOW_GUARD = /-->|(?:^|\n)\s*(?:flowchart|graph)\s+(?:TD|TB|LR|RL)\b/i

const quoteShape = (inner: string, open: string, close: string) => {
  let text = String(inner).trim()
  if (
    (text.startsWith('"') && text.endsWith('"')) ||
    (text.startsWith("'") && text.endsWith("'"))
  ) {
    text = text.slice(1, -1)
  }
  text = text.replace(/"/g, '#quot;')
  return `${open}"${text}"${close}`
}

/** 节点语句挤在一行的拆分：按括号深度/引号/箭头状态机在节点边界补换行 */
const splitGluedMermaid = (source: string) => {
  let out = ''
  let depth = 0
  let inDq = false
  let inSq = false
  let prevWasNodeEnd = false
  let i = 0
  const n = source.length
  const isIdentStart = (c: string) => /[A-Za-z]/.test(c)
  const isIdentPart = (c: string) => /[A-Za-z0-9_-]/.test(c)
  const startsArrow = (idx: number) => {
    if (source.startsWith('==>', idx)) return true
    if (source[idx] !== '-') return false
    let j = idx
    while (j < n && source[j] === '-') j++
    if (source[j] === '.') {
      j++
      while (j < n && source[j] === '-') j++
    }
    return source[j] === '>' || source[j] === '-'
  }
  const consumeArrow = (idx: number) => {
    let j = idx
    while (j < n && /[-=.]/.test(source[j])) j++
    if (source[j] === '>') j++
    return Math.max(j, idx + 1)
  }
  while (i < n) {
    const c = source[i]
    if (c === '\n') {
      out += c
      prevWasNodeEnd = false
      i++
      continue
    }
    if (!inDq && !inSq) {
      if (c === '[' || c === '{' || c === '(') {
        depth++
        out += c
        i++
        continue
      }
      if ((c === ']' || c === '}' || c === ')') && depth > 0) {
        depth--
        out += c
        if (depth === 0) prevWasNodeEnd = true
        i++
        continue
      }
    }
    if (depth > 0) {
      if (c === '"' && !inSq) inDq = !inDq
      else if (c === "'" && !inDq) inSq = !inSq
      out += c
      i++
      continue
    }
    if (c === '"' && !inSq) {
      inDq = !inDq
      out += c
      i++
      continue
    }
    if (c === "'" && !inDq) {
      inSq = !inSq
      out += c
      i++
      continue
    }
    if (!inDq && !inSq && startsArrow(i)) {
      prevWasNodeEnd = false
      const end = consumeArrow(i)
      out += source.slice(i, end)
      i = end
      continue
    }
    if (!inDq && !inSq && isIdentStart(c)) {
      if (prevWasNodeEnd) {
        out = out.replace(/ +$/, '')
        out += '\n'
      }
      const start = i
      i++
      while (i < n && isIdentPart(source[i])) i++
      const ident = source.slice(start, i)
      const dirToken = /^(?:TD|TB|BT|LR|RL)$/i.test(ident)
      if (dirToken && (!out || out.endsWith('\n'))) {
        while (i < n && source[i] === ' ') i++
        prevWasNodeEnd = false
        continue
      }
      out += ident
      prevWasNodeEnd = true
      continue
    }
    out += c
    i++
  }
  return out
}

export const sanitizeMermaid = (code: string) => {
  let source = code.replace(/^\uFEFF/, '').replace(/\r\n/g, '\n').trim()
  if (
    /^mermaidflowchart\b/i.test(source) ||
    /^mermaid\s+flowchart\b/i.test(source)
  ) {
    source = source.replace(/^mermaid\s*/i, '')
  }
  const header = source.match(/^(flowchart|graph)(?:\s+(TD|TB|BT|RL|LR))?\b[ \t]*/i)
  if (header) {
    const kind = header[1]
    const dir = header[2] || 'TD'
    let rest = source.slice(header[0].length)
    rest = rest.replace(/^(?:TD|TB|BT|RL|LR)\b[ \t]+(?=[A-Za-z])/i, '')
    rest = rest.replace(/^\s+/, '')
    source = `${kind} ${dir}\n${rest}`
  }
  const headerBreak = source.indexOf('\n')
  if (headerBreak >= 0) {
    source =
      source.slice(0, headerBreak) +
      '\n' +
      splitGluedMermaid(source.slice(headerBreak + 1)).replace(/^[ \t]+/gm, '')
  }
  source = source.replace(/\bend(?=\s*[[({])/g, 'endNode')
  source = source.replace(/-->\|([^|\n]+)\|/g, (_m, label: string) => {
    let text = String(label).trim()
    if (text.startsWith('"') && text.endsWith('"')) {
      text = text.slice(1, -1)
    }
    return `-->|"${text.replace(/"/g, '#quot;')}"|`
  })
  source = source.replace(
    /([A-Za-z][\w-]*)\[([^\]\n]+)\]/g,
    (_m, id: string, inner: string) => `${id}${quoteShape(inner, '[', ']')}`
  )
  source = source.replace(
    /([A-Za-z][\w-]*)\{([^{\n]+)\}/g,
    (_m, id: string, inner: string) => `${id}${quoteShape(inner, '{', '}')}`
  )
  return source
}
