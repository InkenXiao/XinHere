// WorkBuddy 风格输入区（Xin语首屏与会话视图共用）
// 工具条：左 = ＋文件 / 知识库(多选) / 联网搜索 / 技能；右 = 模型 / 录音 / 发送|停止
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, apiUpload } from '@/transport/api'
import type { KbSource, ModelOption, SendOptions } from '@/types'

interface UploadedFile {
  name: string // 服务端存储名（发送携带）
  displayName: string
  size: number
}

interface SkillItem {
  id: number
  name: string
  description?: string
  category?: string
  selId: string // 发送给后端的技能标识（local:<key> / xuanpu:<name>）
  source: 'local' | 'market'
}

function skillDisplayName(selId: string) {
  return selId.replace(/^(local|xuanpu):/, '')
}

interface Props {
  variant: 'hero' | 'chat'
  disabled?: boolean
  sending?: boolean
  onCancel?: () => void
  onSend: (text: string, opts: SendOptions) => void
}

// 录音器模块级单例：组件重挂载不中断
let recStream: MediaStream | null = null
let recRecorder: MediaRecorder | null = null
let recChunks: Blob[] = []

function fileIcon(name: string) {
  const ext = (name.split('.').pop() || '').toLowerCase()
  if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'].includes(ext)) return '🖼'
  if (ext === 'pdf') return '📄'
  return '📎'
}

export default function ChatComposer({ variant, disabled, sending, onCancel, onSend }: Props) {
  const [text, setText] = useState('')
  const [files, setFiles] = useState<UploadedFile[]>([])
  const [uploading, setUploading] = useState(false)
  const [kbSources, setKbSources] = useState<KbSource[] | null>(null)
  const [kbSel, setKbSel] = useState<Set<string>>(new Set())
  const [webSearch, setWebSearch] = useState(false)
  const [models, setModels] = useState<ModelOption[]>([])
  const [modelsLoaded, setModelsLoaded] = useState(false)
  const [model, setModel] = useState('') // '' = 标准
  const [skills, setSkills] = useState<SkillItem[] | null>(null)
  const [skillSel, setSkillSel] = useState('') // '' = 未选技能
  const [pop, setPop] = useState<'kb' | 'model' | 'skill' | null>(null)
  const [recActive, setRecActive] = useState(false)
  const [recBusy, setRecBusy] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  // 点击弹层外部收起
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setPop(null)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const kbGroups = useMemo(() => {
    const list = kbSources ?? []
    const roots = list.filter((x) => x.parent_id === null)
    return roots.map((r) => ({ root: r, leaves: list.filter((x) => x.parent_id === r.kb_id) }))
  }, [kbSources])

  const currentModelLabel = models.find((m) => m.key === model)?.label ?? '标准'

  const togglePop = (p: 'kb' | 'model' | 'skill') => {
    const next = pop === p ? null : p
    setPop(next)
    if (next === 'kb' && kbSources === null) {
      api<{ items: KbSource[] }>('GET', '/kb/sources')
        .then((r) => setKbSources(r.items ?? []))
        .catch(() => setKbSources([]))
    }
    if (next === 'model' && !modelsLoaded) {
      api<{ items: ModelOption[] }>('GET', '/models')
        .then((r) => setModels(r.items ?? []))
        .catch(() => setModels([]))
        .finally(() => setModelsLoaded(true))
    }
    if (next === 'skill' && skills === null) {
      // 本地技能 + 技能市场合并展示；任一来源失败不影响另一来源
      void Promise.allSettled([
        api<{ items: { skill_key: string; name: string; desc?: string }[] }>('GET', '/skills/local'),
        api<{ items: Omit<SkillItem, 'selId' | 'source'>[] }>('GET', '/xuanpu/skills'),
      ]).then(([local, market]) => {
        const localItems: SkillItem[] = (local.status === 'fulfilled' ? local.value.items ?? [] : []).map(
          (s, i) => ({
            id: -(i + 1),
            name: s.name,
            description: s.desc,
            category: '本地',
            selId: `local:${s.skill_key}`,
            source: 'local' as const,
          }),
        )
        const marketItems: SkillItem[] = (market.status === 'fulfilled' ? market.value.items ?? [] : []).map(
          (s) => ({ ...s, selId: `xuanpu:${s.name}`, source: 'market' as const }),
        )
        setSkills([...localItems, ...marketItems])
      })
    }
  }

  const toggleKb = (id: string) => {
    const n = new Set(kbSel)
    if (n.has(id)) n.delete(id)
    else n.add(id)
    setKbSel(n)
  }

  const uploadFiles = async (list: FileList | File[]) => {
    setUploading(true)
    try {
      for (const f of Array.from(list)) {
        const res = await apiUpload<{ ok: boolean; name: string; display_name: string; size: number }>(
          '/files/upload',
          f,
          f.name,
        )
        setFiles((cur) => [...cur, { name: res.name, displayName: res.display_name || f.name, size: res.size }])
      }
    } catch (e) {
      // 上传失败提示交由调用方 toast（此处简单 alert 语义替换为状态行）
      setFiles((cur) => cur)
      throw e
    } finally {
      setUploading(false)
    }
  }

  /** 录音：点击开始，再次点击停止并转写文字追加到输入框 */
  const toggleRecord = async () => {
    if (recBusy) return
    if (recActive) {
      if (recRecorder && recRecorder.state === 'recording') recRecorder.stop()
      return
    }
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      alert('当前浏览器不支持录音')
      return
    }
    try {
      recStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      alert('无法访问麦克风，请检查浏览器权限')
      return
    }
    recChunks = []
    const rec = new MediaRecorder(recStream)
    rec.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) recChunks.push(e.data)
    }
    rec.onstop = async () => {
      recStream?.getTracks().forEach((t) => t.stop())
      recStream = null
      setRecActive(false)
      const blob = new Blob(recChunks, { type: rec.mimeType || 'audio/webm' })
      recChunks = []
      if (blob.size < 800) return
      setRecBusy(true)
      try {
        const ext = (rec.mimeType || 'audio/webm').includes('ogg') ? 'ogg' : 'webm'
        const res = await apiUpload<{ ok: boolean; text?: string; error?: string }>(
          '/audio/transcribe',
          blob,
          `clip.${ext}`,
        )
        if (res.ok && res.text && res.text.trim()) {
          setText((cur) => (cur ? `${cur}\n${res.text!.trim()}` : res.text!.trim()))
          taRef.current?.focus()
        } else {
          alert(res.error || '未识别到语音内容')
        }
      } catch (e) {
        alert(`转写失败：${e instanceof Error ? e.message : String(e)}`)
      } finally {
        setRecBusy(false)
      }
    }
    rec.start()
    recRecorder = rec
    setRecActive(true)
  }

  const doSend = () => {
    if (disabled || sending) return
    let t = text.trim()
    if (!t && files.length === 0) return
    if (!t) t = '请帮我解析并分析上传的附件'
    onSend(t, {
      kbIds: kbSel.size > 0 ? [...kbSel] : undefined,
      webSearch: webSearch || undefined,
      model: model || undefined,
      fileNames: files.length > 0 ? files.map((f) => f.name) : undefined,
      skill: skillSel || undefined,
    })
    setText('')
    if (taRef.current) taRef.current.style.height = ''
    setFiles([])
  }

  const busy = uploading || recBusy

  return (
    <div className={`cmp cmp-${variant}`} ref={rootRef}>
      {(files.length > 0 || skillSel) && (
        <div className="cmp-chips">
          {skillSel && (
            <span className="cmp-chip" title="本条消息将按该技能的流程执行">
              <span>⚙</span>
              <span className="cmp-chip-name">{skillDisplayName(skillSel)}</span>
              <span className="cmp-chip-x" title="移除" onClick={() => setSkillSel('')}>
                ×
              </span>
            </span>
          )}
          {files.map((f) => (
            <span className="cmp-chip" key={f.name} title={f.displayName}>
              <span>{fileIcon(f.displayName)}</span>
              <span className="cmp-chip-name">{f.displayName}</span>
              <span
                className="cmp-chip-x"
                title="移除"
                onClick={() => setFiles((cur) => cur.filter((x) => x.name !== f.name))}
              >
                ×
              </span>
            </span>
          ))}
        </div>
      )}
      <textarea
        ref={taRef}
        rows={variant === 'hero' ? 2 : 1}
        placeholder={
          disabled
            ? '请先选择或新建会话'
            : recActive
              ? '录音中…再次点击麦克风结束并转写为文字'
              : variant === 'hero'
                ? '向 XinHere 提问…'
                : '输入消息，Enter 发送 / Shift+Enter 换行'
        }
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onInput={(e) => {
          const el = e.currentTarget
          el.style.height = 'auto'
          el.style.height = `${Math.min(el.scrollHeight, variant === 'hero' ? 200 : 140)}px`
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            doSend()
          }
        }}
      />
      <div className="cmp-bar">
        <div className="cmp-bar-left">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            style={{ display: 'none' }}
            onChange={(e) => {
              if (e.target.files && e.target.files.length > 0) {
                void uploadFiles(e.target.files).catch(() => alert('附件上传失败，请重试'))
              }
              e.target.value = ''
            }}
          />
          <button
            type="button"
            className={`cmp-btn ${uploading ? 'busy' : ''}`}
            title="上传附件（图片/PDF/文本将自动解析）"
            disabled={disabled || uploading}
            onClick={() => fileInputRef.current?.click()}
          >
            {uploading ? '⏳ 上传中' : '＋ 文件'}
          </button>
          <button
            type="button"
            className={`cmp-btn ${kbSel.size > 0 ? 'on' : ''} ${pop === 'kb' ? 'open' : ''}`}
            title="选择知识库（回答时优先检索所选知识库）"
            disabled={disabled}
            onClick={() => togglePop('kb')}
          >
            📚 知识库{kbSel.size > 0 && <span className="cmp-badge">{kbSel.size}</span>}
          </button>
          <button
            type="button"
            className={`cmp-btn ${webSearch ? 'on' : ''}`}
            title="开启后本条消息可联网搜索"
            disabled={disabled}
            onClick={() => setWebSearch((v) => !v)}
          >
            🌐 联网搜索
          </button>
          <button
            type="button"
            className={`cmp-btn ${skillSel ? 'on' : ''} ${pop === 'skill' ? 'open' : ''}`}
            title="选择技能（本条消息将按该技能的流程执行）"
            disabled={disabled}
            onClick={() => togglePop('skill')}
          >
            ⚙ 技能
          </button>
        </div>
        <div className="cmp-bar-right">
          <button
            type="button"
            className={`cmp-btn ${model ? 'on' : ''} ${pop === 'model' ? 'open' : ''}`}
            title="选择模型"
            disabled={disabled}
            onClick={() => togglePop('model')}
          >
            ⚡ {currentModelLabel} ▾
          </button>
          <button
            type="button"
            className={`cmp-btn cmp-btn-mic ${recActive ? 'rec' : ''}`}
            title={recActive ? '点击停止录音' : '点击开始录音，转写为文字'}
            disabled={disabled || busy}
            onClick={() => void toggleRecord()}
          >
            {recBusy ? '⏳ 转写中' : recActive ? '⏺ 录音中' : '🎤'}
          </button>
          {sending ? (
            <button type="button" className="cmp-send stop" onClick={() => onCancel?.()}>
              停止
            </button>
          ) : (
            <button
              type="button"
              className="cmp-send"
              disabled={disabled || busy || (!text.trim() && files.length === 0)}
              onClick={doSend}
            >
              发送
            </button>
          )}
        </div>
      </div>

      {/* 知识库多选弹层 */}
      {pop === 'kb' && (
        <div className="cmp-pop cmp-pop-kb">
          <div className="cmp-pop-head">
            <span>选择知识库</span>
            <span className="cmp-pop-sub">回答时优先检索所选知识库</span>
            <button className="cmp-pop-x" onClick={() => setPop(null)}>
              ×
            </button>
          </div>
          <div className="cmp-pop-list">
            {kbSources === null && <div className="cmp-pop-empty">加载中…</div>}
            {kbSources !== null && kbGroups.length === 0 && (
              <div className="cmp-pop-empty">暂无可选知识库</div>
            )}
            {kbGroups.map(({ root, leaves }) =>
              leaves.length === 0 ? (
                <KbLeaf key={root.kb_id} src={root} sel={kbSel.has(root.kb_id)} onToggle={toggleKb} />
              ) : (
                <div key={root.kb_id}>
                  <div className="cmp-kb-group">{root.name}</div>
                  {leaves.map((l) => (
                    <KbLeaf key={l.kb_id} src={l} sel={kbSel.has(l.kb_id)} onToggle={toggleKb} />
                  ))}
                </div>
              ),
            )}
          </div>
          <div className="cmp-pop-foot">
            <button className="cmp-pop-link" onClick={() => setKbSel(new Set())}>
              清空
            </button>
            <button className="cmp-pop-ok" onClick={() => setPop(null)}>
              完成
            </button>
          </div>
        </div>
      )}

      {/* 模型选择弹层 */}
      {pop === 'model' && (
        <div className="cmp-pop cmp-pop-model">
          <div className="cmp-pop-head">
            <span>选择模型</span>
            <button className="cmp-pop-x" onClick={() => setPop(null)}>
              ×
            </button>
          </div>
          <div className="cmp-pop-list">
            {models.length === 0 && <div className="cmp-pop-empty">暂无可用模型</div>}
            {models.map((m) => (
              <label className={`cmp-pop-item ${model === m.key ? 'sel' : ''}`} key={m.key}>
                <input
                  type="radio"
                  name="cmp-model"
                  checked={model === m.key}
                  onChange={() => {
                    setModel(m.key)
                    setPop(null)
                  }}
                />
                <span className="cmp-pop-name">⚡ {m.label}</span>
                <span className="cmp-pop-tag" title={m.model}>
                  {m.model}
                </span>
              </label>
            ))}
          </div>
        </div>
      )}

      {/* 技能选择弹层（单选，选中后本条消息按技能流程执行） */}
      {pop === 'skill' && (
        <div className="cmp-pop cmp-pop-skill">
          <div className="cmp-pop-head">
            <span>选择技能</span>
            <span className="cmp-pop-sub">发送后按所选技能的流程执行</span>
            <button className="cmp-pop-x" onClick={() => setPop(null)}>
              ×
            </button>
          </div>
          <div className="cmp-pop-list">
            {skills === null && <div className="cmp-pop-empty">加载中…</div>}
            {skills !== null && skills.length === 0 && (
              <div className="cmp-pop-empty">暂无可用技能</div>
            )}
            {(['local', 'market'] as const).map((src) => {
              const group = skills?.filter((s) => s.source === src) ?? []
              if (group.length === 0) return null
              return (
                <div key={src}>
                  <div className="cmp-kb-group">{src === 'local' ? '本地技能' : '技能市场'}</div>
                  {group.map((s) => (
                    <label
                      className={`cmp-pop-item ${skillSel === s.selId ? 'sel' : ''}`}
                      key={s.selId}
                      title={s.description || s.name}
                    >
                      <input
                        type="radio"
                        name="cmp-skill"
                        checked={skillSel === s.selId}
                        onChange={() => {
                          setSkillSel(skillSel === s.selId ? '' : s.selId)
                          setPop(null)
                        }}
                      />
                      <span className="cmp-pop-name">⚙ {s.name}</span>
                      <span className="cmp-pop-tag">
                        {(s.description || '').slice(0, 40) || s.category || ''}
                      </span>
                    </label>
                  ))}
                </div>
              )
            })}
          </div>
          <div className="cmp-pop-foot">
            <button
              className="cmp-pop-link"
              onClick={() => {
                setSkillSel('')
                setPop(null)
              }}
            >
              不使用技能
            </button>
            <button className="cmp-pop-ok" onClick={() => setPop(null)}>
              完成
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function KbLeaf({ src, sel, onToggle }: { src: KbSource; sel: boolean; onToggle: (id: string) => void }) {
  return (
    <div className={`cmp-kb-leaf ${sel ? 'sel' : ''}`} onClick={() => onToggle(src.kb_id)}>
      <span className="cmp-kb-cbx">{sel ? '✓' : ''}</span>
      <span className="cmp-kb-name" title={src.name}>
        {src.name}
      </span>
      <span className={`cmp-kb-badge ${src.kb_type === 'internal' ? 'h' : 'n'}`}>
        {src.kb_type === 'internal' ? '内部' : '外部'}
      </span>
    </div>
  )
}
