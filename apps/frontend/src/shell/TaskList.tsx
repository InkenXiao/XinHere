// 历史任务列表：按模式加载（Xin语=AI 对话记录 / Xin台=AI 能力执行记录）
// 树形层级：拖拽上/下沿=同层排序，拖至中部=移入文件夹；两个记录重叠即创建文件夹
// 支持：文件夹展开、重命名、删除（文件夹级联）、点击打开对应页面
import { useEffect, useState } from 'react'
import { api } from '@/transport/api'
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'
import type { TaskRecordItem } from '@/types'
import { fmtTime } from '@/utils'

type DropPos = 'before' | 'after' | 'inside'

const STATUS_LABEL: Record<string, string> = {
  running: '运行中',
  success: '已完成',
  failed: '未完成',
  stopped: '已停止',
}

export default function TaskList() {
  const mode = useUiStore((s) => s.mode)
  const historyOpen = useUiStore((s) => s.historyOpen)
  const toast = useUiStore((s) => s.toast)
  const [items, setItems] = useState<TaskRecordItem[]>([])
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [dragId, setDragId] = useState<string | null>(null)
  const [dropHint, setDropHint] = useState<{ id: string; pos: DropPos } | null>(null)

  const scope = mode === 'cockpit' ? 'exec' : 'chat'

  const load = async () => {
    try {
      const r = await api<{ items: TaskRecordItem[] }>('GET', `/tasks?scope=${scope}`)
      setItems(r.items ?? [])
    } catch {
      /* 静默：抽屉内失败即空态 */
    }
  }

  useEffect(() => {
    if (historyOpen) void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyOpen, scope])

  const childrenOf = (parentId: string | null) =>
    items.filter((i) => (i.parent_id ?? null) === parentId)

  const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

  const commit = async (parentId: string | null, ids: string[]) => {
    try {
      await api('POST', '/tasks/reorder', { parent_id: parentId, ids })
      await load()
    } catch (e: unknown) {
      toast(errMsg(e), 'err')
    }
  }

  /* 拖拽落点：上沿=前插 / 下沿=后插 / 中部=入文件夹（非文件夹目标则重叠建文件夹） */
  const onDropItem = async (target: TaskRecordItem, pos: DropPos) => {
    const id = dragId
    setDragId(null)
    setDropHint(null)
    if (!id || id === target.id) return
    const dragged = items.find((i) => i.id === id)
    if (!dragged) return
    if (pos === 'inside') {
      if (target.kind === 'folder') {
        const sibs = childrenOf(target.id).map((i) => i.id).filter((x) => x !== id)
        await commit(target.id, [...sibs, id])
        setExpanded((s) => new Set(s).add(target.id))
        return
      }
      // 重叠 → 建文件夹，两者移入
      try {
        const folder = await api<TaskRecordItem>('POST', '/tasks/folders', {
          title: '新建文件夹',
          scope,
          parent_id: target.parent_id ?? null,
        })
        const layer = childrenOf(target.parent_id ?? null)
        const idx = layer.findIndex((i) => i.id === target.id)
        const ids = layer.map((i) => i.id).filter((x) => x !== id && x !== target.id)
        ids.splice(Math.max(idx, 0), 0, folder.id)
        await api('POST', '/tasks/reorder', { parent_id: target.parent_id ?? null, ids })
        await api('POST', '/tasks/reorder', { parent_id: folder.id, ids: [target.id, id] })
        await load()
        setExpanded((s) => new Set(s).add(folder.id))
      } catch (e: unknown) {
        toast(errMsg(e), 'err')
      }
      return
    }
    const parentId = target.parent_id ?? null
    const layer = childrenOf(parentId).map((i) => i.id).filter((x) => x !== id)
    const idx = layer.indexOf(target.id)
    layer.splice(pos === 'before' ? idx : idx + 1, 0, id)
    await commit(parentId, layer)
  }

  const saveRename = async () => {
    const id = editingId
    if (!id) return
    const title = editTitle.trim()
    setEditingId(null)
    if (!title) return
    try {
      await api('PATCH', `/tasks/${id}`, { title })
      await load()
    } catch (e: unknown) {
      toast(errMsg(e), 'err')
    }
  }

  const remove = async (r: TaskRecordItem) => {
    if (!window.confirm(r.kind === 'folder' ? `删除文件夹「${r.title}」及其内容？` : `删除「${r.title}」？`)) return
    try {
      await api('DELETE', `/tasks/${r.id}`)
      await load()
    } catch (e: unknown) {
      toast(errMsg(e), 'err')
    }
  }

  /* 点击打开：chat → 回 Xin语载入会话；exec → 详情弹窗；folder → 展开 */
  const openItem = (r: TaskRecordItem) => {
    if (editingId) return
    if (r.kind === 'folder') {
      setExpanded((s) => {
        const n = new Set(s)
        if (n.has(r.id)) n.delete(r.id)
        else n.add(r.id)
        return n
      })
      return
    }
    if (r.kind === 'chat' && r.ref_id) {
      const ui = useUiStore.getState()
      if (ui.mode !== 'tower') ui.setMode('tower')
      ui.setHistoryOpen(false)
      ui.setWorkView('chat')
      void useSessionStore.getState().openSession(r.ref_id)
      return
    }
    useUiStore.getState().openExecDetail(r)
  }

  /* 「新任务」：回到当前模式页面的初始状态（实时会议录音状态保持不变） */
  const newTask = () => {
    if (mode === 'cockpit') {
      useUiStore.setState({ kanbanOpen: false, meetingOpen: false, execDetail: null })
    } else {
      useSessionStore.getState().resetSession()
    }
    useUiStore.getState().setHistoryOpen(false)
  }

  const hintCls = (r: TaskRecordItem) => {
    if (!dropHint || dropHint.id !== r.id) return ''
    return dropHint.pos
  }

  const renderRow = (r: TaskRecordItem, depth: number) => {
    const isEditing = editingId === r.id
    const hint = hintCls(r)
    return (
      <div key={r.id}>
        <div
          className={`tl-item ${r.kind} ${hint}`}
          style={{ paddingLeft: 10 + depth * 16 }}
          draggable={!isEditing}
          onDragStart={(e) => {
            setDragId(r.id)
            e.dataTransfer.effectAllowed = 'move'
            e.dataTransfer.setData('text/plain', r.id)
          }}
          onDragEnd={() => {
            setDragId(null)
            setDropHint(null)
          }}
          onDragOver={(e) => {
            if (!dragId || dragId === r.id) return
            e.preventDefault()
            e.stopPropagation()
            const rect = e.currentTarget.getBoundingClientRect()
            const ratio = (e.clientY - rect.top) / rect.height
            const pos: DropPos = ratio < 0.25 ? 'before' : ratio > 0.75 ? 'after' : 'inside'
            if (dropHint?.id !== r.id || dropHint?.pos !== pos) setDropHint({ id: r.id, pos })
          }}
          onDragLeave={(e) => {
            e.stopPropagation()
            if (dropHint?.id === r.id) setDropHint(null)
          }}
          onDrop={(e) => {
            e.preventDefault()
            e.stopPropagation()
            void onDropItem(r, dropHint?.id === r.id ? dropHint.pos : 'inside')
          }}
        >
          {r.kind === 'folder' ? (
            <span className={`tl-caret ${expanded.has(r.id) ? 'open' : ''}`} aria-hidden="true">
              ▸
            </span>
          ) : (
            <span className="tl-caret spacer" aria-hidden="true" />
          )}
          <span className={`tl-kind ${r.kind}`}>
            {r.kind === 'folder' ? '▢' : r.kind === 'chat' ? '●' : '◆'}
          </span>
          {isEditing ? (
            <input
              className="tl-edit"
              value={editTitle}
              autoFocus
              onChange={(e) => setEditTitle(e.target.value)}
              onBlur={() => void saveRename()}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void saveRename()
                if (e.key === 'Escape') setEditingId(null)
              }}
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <>
              <span className="tl-title" title={r.title} onClick={() => openItem(r)}>
                {r.title || '未命名'}
              </span>
              {r.kind === 'exec' && r.status && STATUS_LABEL[r.status] && (
                <span className={`tl-status ${r.status}`}>{STATUS_LABEL[r.status]}</span>
              )}
              <span className="tl-time">{fmtTime(r.created_at || '')}</span>
              <span className="tl-ops">
                <button
                  className="op"
                  title="重命名"
                  onClick={(e) => {
                    e.stopPropagation()
                    setEditingId(r.id)
                    setEditTitle(r.title)
                  }}
                >
                  ✎
                </button>
                <button
                  className="op del"
                  title="删除"
                  onClick={(e) => {
                    e.stopPropagation()
                    void remove(r)
                  }}
                >
                  ×
                </button>
              </span>
            </>
          )}
        </div>
        {r.kind === 'folder' && expanded.has(r.id) && (
          <div className="tl-children">
            {childrenOf(r.id).map((c) => renderRow(c, depth + 1))}
          </div>
        )}
      </div>
    )
  }

  return (
    <>
      <button className="hist-new" onClick={newTask}>
        + 新任务
      </button>
      <div
        className="hist-list tl-list"
        onDragOver={(e) => {
          if (dragId) e.preventDefault()
        }}
        onDrop={(e) => {
          // 落到空白处 → 移至顶层末尾
          if (!dragId) return
          e.preventDefault()
          const top = childrenOf(null).map((i) => i.id).filter((x) => x !== dragId)
          void commit(null, [...top, dragId])
          setDragId(null)
          setDropHint(null)
        }}
      >
        {childrenOf(null).map((r) => renderRow(r, 0))}
        {childrenOf(null).length === 0 && (
          <div className="hist-empty">{scope === 'exec' ? '暂无执行记录' : '暂无对话记录'}</div>
        )}
      </div>
    </>
  )
}
