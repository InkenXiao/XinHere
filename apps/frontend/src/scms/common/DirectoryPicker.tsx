// 组织目录选择器（三模式共享）：组织架构树 / 用户列表 / 全部分组
// - 树：部门层级展开；点部门名 = 选中整个子树（expand 的 dept 语义即子树展开），点用户 = 选人
// - 用户列表：平铺可搜索；全部分组：用户/角色/部门/公司/用户组（原 MentionInput 行为）
// - 多选由调用方累积（连续 onPick）；excludedIds 置灰已在任务中的用户
// - 键盘：调用方把 textarea keydown 交给 keyRef.current.handleKey，↑↓ 移动高亮、Enter 选中；
//   Esc 不在此消费（调用方负责关闭面板）
import { useEffect, useImperativeHandle, useRef, useState, type KeyboardEvent, type MouseEvent, type RefObject } from 'react'
import { api } from '@/scms/api'
import type { DirectoryEntry, OrgTreeNode, OrgTreeResp } from '@/scms/types'

export interface PickTarget {
  e: DirectoryEntry
  type: string // user | role | dept | company | group
  groupZh: string
}

export interface PickerHandle {
  handleKey: (e: KeyboardEvent) => boolean
}

export type DirectoryPickerMode = 'tree' | 'user' | 'role' | 'group'
type Mode = DirectoryPickerMode

const MODES: { key: Mode; zh: string }[] = [
  { key: 'tree', zh: '组织架构' },
  { key: 'user', zh: '用户' },
  { key: 'role', zh: '角色' },
  { key: 'group', zh: '分组' },
]

const GROUPS: { key: string; zh: string }[] = [
  { key: 'user', zh: '用户' },
  { key: 'role', zh: '角色' },
  { key: 'dept', zh: '部门' },
  { key: 'company', zh: '公司' },
  { key: 'group', zh: '用户组' },
]

/** 键盘导航用的扁平可见行（树模式 = 展开后的先序遍历） */
interface Row {
  key: string
  target: PickTarget
  depth: number
  node?: OrgTreeNode // 有 = 部门行（可展开）
  expanded?: boolean
  tag?: string // 分组头标签（分组模式）
  disabled?: boolean
}

export default function DirectoryPicker({
  q,
  onPick,
  excludedIds,
  keyRef,
  maxHeight = 260,
  hideGroup = false,
  selectedTargetKeys,
  onClose,
  defaultMode = 'tree',
}: {
  q: string
  onPick: (t: PickTarget) => void
  excludedIds?: Set<string>
  keyRef?: RefObject<PickerHandle | null>
  maxHeight?: number
  hideGroup?: boolean
  selectedTargetKeys?: Set<string>
  onClose?: () => void
  defaultMode?: DirectoryPickerMode
}) {
  const [mode, setMode] = useState<Mode>(defaultMode)
  const [groups, setGroups] = useState<Record<string, DirectoryEntry[]>>({})
  const [tree, setTree] = useState<OrgTreeResp | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [hi, setHi] = useState(0)
  const [selectedRole, setSelectedRole] = useState<DirectoryEntry | null>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const selectedKey = (type: string, id: string) => selectedTargetKeys?.has(`${type}:${id}`) ?? false

  // 数据源按模式切换（q 变化 200ms 防抖）；搜索态树默认全展开，靠 matched_users 兜底
  useEffect(() => {
    setHi(0)
    const t = setTimeout(() => {
      if (mode === 'tree') {
        api<OrgTreeResp>('GET', `/org/tree?q=${encodeURIComponent(q)}`, undefined, { silent: true })
          .then((r) => setTree(r.data))
          .catch(() => setTree(null))
      } else {
        const ts = mode === 'user' ? '&types=user' : mode === 'role' ? selectedRole ? `&types=user&role_code=${encodeURIComponent(selectedRole.id)}` : '&types=role' : ''
        api<Record<string, DirectoryEntry[]>>(
          'GET',
          `/org/directory?q=${encodeURIComponent(q)}${ts}`,
          undefined,
          { silent: true },
        )
          .then((r) => setGroups(r.data ?? {}))
          .catch(() => setGroups({}))
      }
    }, 200)
    return () => clearTimeout(t)
  }, [q, mode, selectedRole])

  const searching = q.trim().length > 0

  const rows: Row[] = []
  if (mode === 'tree') {
    if (tree?.matched_users.length && searching) {
      rows.push({
        key: 'mu-h',
        depth: 0,
        target: { e: { id: '', label: '' }, type: 'user', groupZh: '' },
        tag: `搜索到 ${tree.matched_users.length} 人`,
      })
    }
    const walk = (node: OrgTreeNode, depth: number) => {
      const isOpen = searching || expanded.has(node.id)
      const dup = excludedIds?.has(node.id) ?? false
      rows.push({
        key: `n:${node.id}`,
        depth,
        node,
        expanded: isOpen,
        target: { e: { id: node.id, label: node.label, extra: `${node.count} 人` }, type: 'dept', groupZh: '部门' },
        disabled: dup,
      })
      if (isOpen) {
        for (const u of node.users) {
          rows.push({
            key: `u:${node.id}:${u.id}`,
            depth: depth + 1,
            target: { e: u, type: 'user', groupZh: '用户' },
            disabled: excludedIds?.has(u.id) ?? false,
          })
        }
        for (const ch of node.children) walk(ch, depth + 1)
      }
    }
    for (const r of tree?.tree ?? []) walk(r, 0)
  } else if (mode === 'role') {
    const type = selectedRole ? 'user' : 'role'
    for (const e of groups[type] ?? []) rows.push({ key: `${type}:${e.id}`, depth: 0, target: { e, type, groupZh: type === 'role' ? '角色' : '用户' }, tag: type === 'role' ? '角色' : '用户', disabled: type === 'user' && (excludedIds?.has(e.id) ?? false) })
  } else {
    for (const g of GROUPS) {
      const list = groups[g.key] ?? []
      for (const e of list) {
        rows.push({
          key: `${g.key}:${e.id}`,
          depth: 0,
          target: { e, type: g.key, groupZh: g.zh },
          tag: g.zh,
          disabled: g.key === 'user' && (excludedIds?.has(e.id) ?? false),
        })
      }
    }
  }
  const hiClamped = rows.length === 0 ? 0 : Math.min(hi, rows.length - 1)

  useImperativeHandle(keyRef, () => ({
    handleKey(e: KeyboardEvent) {
      if (rows.length === 0) return false
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setHi((h) => (Math.min(h, rows.length - 1) + 1) % rows.length)
        return true
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setHi((h) => (Math.min(h, rows.length - 1) - 1 + rows.length) % rows.length)
        return true
      }
      if (e.key === 'Enter') {
        const r = rows[hiClamped]
        if (r.disabled) return false
        e.preventDefault()
        onPick(r.target)
        return true
      }
      return false
    },
  }))

  // 高亮行滚入可视区
  useEffect(() => {
    bodyRef.current?.querySelector('.on')?.scrollIntoView({ block: 'nearest' })
  }, [hiClamped])

  const toggle = (id: string) =>
    setExpanded((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const clickRow = (r: Row, e: MouseEvent) => {
    // 部门行的箭头只展开/收起；点名称 = 选中该部门（整个子树）
    const el = e.target as HTMLElement
    if (r.node && el.classList.contains('pk-tw')) {
      toggle(r.node.id)
      return
    }
    if (r.disabled) return
    if (mode === 'role' && r.target.type === 'role') {
      setSelectedRole(r.target.e)
      return
    }
    onPick(r.target)
  }

  return (
    <div className="pk">
      {onClose && <button type="button" aria-label="关闭选择面板" title="关闭" className="absolute right-2! top-2! z-10 grid size-7 place-items-center rounded-md text-lg text-slate-400 hover:bg-slate-200 hover:text-slate-700" onMouseDown={(event) => event.preventDefault()} onClick={onClose}>×</button>}
      <div className="pk-tabs">
        {MODES.filter((m) => !(hideGroup && m.key === 'group')).map((m) => (
          <span key={m.key} className={`pk-tab ${mode === m.key ? 'on' : ''}`} onClick={() => { setMode(m.key); if (m.key !== 'role') setSelectedRole(null) }}>
            {m.zh}
          </span>
        ))}
      </div>
      {mode === 'role' && selectedRole && <button type="button" className="mx-2! mb-1! text-xs font-medium text-sky-500" onClick={() => setSelectedRole(null)}>← 返回角色列表 · {selectedRole.label}</button>}
      <div className="pk-body" style={{ maxHeight }} ref={bodyRef}>
        {mode === 'role' && selectedRole && <div className="px-2! py-1.5"><label className="flex cursor-pointer items-center gap-2 rounded-lg px-2! py-2! text-base hover:bg-sky-500/10"><input type="checkbox" checked={selectedKey('role', selectedRole.id)} onChange={() => onPick({ e: selectedRole, type: 'role', groupZh: '角色' })} /><span className="font-medium">选择整个角色</span></label></div>}
        {rows.length === 0 && <div className="pk-empty">{searching ? '无匹配结果' : '加载中…'}</div>}
        {rows.map((r, i) => (
          <div
            key={r.key}
            className={`mention-item pk-row ${i === hiClamped ? 'on' : ''} ${r.disabled ? 'off' : ''} ${r.node ? 'is-node' : ''}`}
            style={r.node ? { paddingLeft: 8 + r.depth * 14 } : { paddingLeft: 8 + r.depth * 14 + (r.node ? 0 : 16) }}
            onMouseDown={(e) => e.preventDefault()}
            onClick={(e) => clickRow(r, e)}
          >
            {r.node ? (
              <>
                <span className="pk-tw">{r.expanded ? '▾' : '▸'}</span>
                <span className="mention-label">{r.node.label}</span>
                <span className="pk-cnt">{r.node.count} 人</span>
              </>
            ) : r.tag && !r.target.e.id ? (
              <span className="pk-ghead">{r.tag}</span>
            ) : (
              <>
                {(mode === 'role') && <input type="checkbox" className="mr-1!" checked={selectedKey(r.target.type, r.target.e.id)} onClick={(event) => event.stopPropagation()} onChange={() => onPick(r.target)} />}
                <span className="mention-tag">{r.tag}</span>
                <span className="mention-label">{r.target.e.label}</span>
                {r.target.e.extra && <span className="mention-extra">{r.target.e.extra}</span>}
                {mode === 'role' && r.target.type === 'role' && <span className="ml-auto! text-xs text-sky-500">展开用户 ›</span>}
                {r.disabled && <span className="pk-cnt">已在任务中</span>}
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
