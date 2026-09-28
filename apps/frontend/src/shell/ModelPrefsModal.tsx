// 个人设置弹窗：按模型通道配置个人偏好（未设置的字段走系统默认）
import { useCallback, useEffect, useState } from 'react'
import Modal from '@/primitives/Modal'
import { api, get, put } from '@/transport/api'
import { useUiStore } from '@/state/uiStore'

interface OverrideView {
  api_url: string
  api_key_set: boolean
  model: string
  max_tokens: number
}

interface ChannelInfo {
  api_url: string
  model: string
  max_tokens: number
  override: OverrideView | null
}

interface Draft {
  api_url: string
  api_key: string
  model: string
  max_tokens: string
}

// 通道展示配置：[键, 图标, 名称, 用途说明]
const CHANNELS: Array<[string, string, string, string]> = [
  ['main', '🧠', '主推理模型', '智能体对话、会议纪要、报告生成等主力任务'],
  ['small', '⚡', '轻量快推模型', '意图识别、内容概括、快速润色等轻量任务'],
  ['coder', '💻', '代码生成模型', '代码生成与编程辅助'],
  ['vision', '👁', '视觉识别模型', '图片与表格内容识别'],
]

const EMPTY_DRAFT: Draft = { api_url: '', api_key: '', model: '', max_tokens: '' }

function draftOf(info: ChannelInfo | undefined): Draft {
  const ov = info?.override
  if (!ov) return { ...EMPTY_DRAFT }
  return {
    api_url: ov.api_url || '',
    api_key: '',
    model: ov.model || '',
    max_tokens: ov.max_tokens ? String(ov.max_tokens) : '',
  }
}

export default function ModelPrefsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useUiStore((s) => s.toast)
  const [channels, setChannels] = useState<Record<string, ChannelInfo>>({})
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [loaded, setLoaded] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setErr('')
    try {
      const r = await get<{ channels: Record<string, ChannelInfo> }>('/model-prefs')
      const chans = r.channels ?? {}
      setChannels(chans)
      setDrafts(Object.fromEntries(Object.keys(chans).map((k) => [k, draftOf(chans[k])])))
      setLoaded(true)
    } catch (e) {
      setErr((e as Error).message)
    }
  }, [])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  const setField = (ch: string, patch: Partial<Draft>) =>
    setDrafts((m) => ({ ...m, [ch]: { ...(m[ch] ?? { ...EMPTY_DRAFT }), ...patch } }))

  const wrap = (fn: () => Promise<void>) => async () => {
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      toast((e as Error).message, 'err')
    } finally {
      setBusy(false)
    }
  }

  const save = (ch: string) =>
    wrap(async () => {
      const d = drafts[ch] ?? { ...EMPTY_DRAFT }
      await put(`/model-prefs/${encodeURIComponent(ch)}`, {
        api_url: d.api_url.trim(),
        api_key: d.api_key.trim(),
        model: d.model.trim(),
        max_tokens: parseInt(d.max_tokens, 10) || 0,
      })
      toast('个人配置已保存，对话与生成即时生效')
      await load()
    })

  const reset = (ch: string) =>
    wrap(async () => {
      await api('DELETE', `/model-prefs/${encodeURIComponent(ch)}`)
      toast('已恢复系统默认配置')
      await load()
    })

  if (!open) return null

  return (
    <Modal title="个人设置 · 模型偏好" onClose={onClose}>
      <div className="modal-note">
        按用途为各模型配置自己的服务与参数，保存后对话与生成即时生效；未设置的字段使用系统默认，
        已覆盖的字段清空后保存即恢复默认。仅本人可见。
      </div>
      {err && <div className="modal-note" style={{ color: '#ef4444' }}>加载失败：{err}</div>}
      {!loaded && !err && <div className="modal-note">加载中…</div>}
      {loaded && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {CHANNELS.map(([key, icon, name, desc]) => {
            const info = channels[key]
            const ov = info?.override ?? null
            const d = drafts[key] ?? { ...EMPTY_DRAFT }
            const keyPh = ov?.api_key_set ? '已设置（输入新值可更换；留空保存＝恢复默认）' : '留空使用系统默认'
            return (
              <div
                key={key}
                style={{
                  border: '1px solid var(--glass-bd)', borderRadius: 12, padding: '12px 14px',
                  background: 'var(--glass)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <b style={{ fontSize: 13, color: 'var(--ink-100)' }}>{icon} {name}</b>
                  <span
                    style={{
                      fontSize: 11, padding: '1px 8px', borderRadius: 10,
                      color: ov ? '#fff' : 'var(--ink-50)',
                      background: ov ? 'var(--accent-grad)' : 'transparent',
                      border: ov ? 'none' : '1px solid var(--glass-bd)',
                    }}
                  >
                    {ov ? '个人配置' : '系统默认'}
                  </span>
                </div>
                <div className="modal-note" style={{ margin: '4px 0 10px' }}>
                  {desc}。系统默认：{info?.model || '未配置'} · 单次回复上限 {info?.max_tokens || '未限制'}
                </div>
                <div className="form-row">
                  <label>服务地址</label>
                  <input
                    value={d.api_url}
                    onChange={(e) => setField(key, { api_url: e.target.value })}
                    placeholder="留空使用系统默认"
                    autoComplete="off"
                  />
                </div>
                <div className="form-row">
                  <label>访问密钥</label>
                  <input
                    type="password"
                    value={d.api_key}
                    onChange={(e) => setField(key, { api_key: e.target.value })}
                    placeholder={keyPh}
                    autoComplete="new-password"
                  />
                </div>
                <div className="form-row">
                  <label>模型名称</label>
                  <input
                    value={d.model}
                    onChange={(e) => setField(key, { model: e.target.value })}
                    placeholder="留空使用系统默认"
                    autoComplete="off"
                  />
                </div>
                <div className="form-row">
                  <label>单次回复上限</label>
                  <input
                    type="number"
                    min={0}
                    step={1000}
                    style={{ width: 200 }}
                    value={d.max_tokens}
                    onChange={(e) => setField(key, { max_tokens: e.target.value })}
                    placeholder="留空使用系统默认"
                  />
                  <div className="ff-help">一次回复最多生成的字数规模，0 或留空＝跟随系统默认。</div>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                  <button className="btn-primary" disabled={busy} onClick={() => void save(key)()}>
                    保存
                  </button>
                  {ov && (
                    <button className="btn-ghost" disabled={busy} onClick={() => void reset(key)()}>
                      恢复系统默认
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Modal>
  )
}
