// Xin台主界面（日·守）：分组卡片（智能体应用 / AI 促研 / AI 工具 / 业务系统），全部来自服务端配置
// link 卡=页面跳转（免登）；task 卡=后台执行技能；meeting 卡=实时会议（录音后台运行）
// placeholder 卡=功能占位（soon=二期置灰）；卡片副标题一行功能描述
// 编辑模式（仅本部财务，经右上角用户菜单「页面编辑」开关开启）：
// 拖拽卡片排序 / 删除卡片 / 添加卡片，配置实时落库
import { useEffect, useState } from 'react'
import { api } from '@/transport/api'
import { useUiStore } from '@/state/uiStore'
import { useAuthStore } from '@/state/authStore'
import { useMeetingStore } from '@/state/meetingStore'
import { useTaskRunStore } from '@/state/taskRunStore'
import GraphCard from './GraphCard'
import CockpitCardModal from './CockpitCardModal'
import ScmsProvider from '@/scms/ScmsProvider'
import { TemplateCreateModal } from '@/scms/modules/dynamic-template/components/TemplateCreateModal'
import type { CockpitCardGroup, CockpitCardItem } from '@/types'

function fmtDur(sec: number) {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function CardView({
  card,
  editing,
  onOpenLink,
}: {
  card: CockpitCardItem
  editing?: boolean
  onOpenLink: (c: CockpitCardItem) => void
}) {
  const toast = useUiStore((s) => s.toast)
  const perms = useAuthStore((s) => s.user?.permissions ?? [])
  const canMeeting = perms.includes('xinhere.meeting')
  const canSkill = perms.includes('xinhere.skill.run')
  const setMeetingOpen = useUiStore((s) => s.setMeetingOpen)
  const startRun = useTaskRunStore((s) => s.startRun)
  const run = useTaskRunStore((s) => s.runs.find((r) => r.cardKey === card.key) ?? null)
  const busy = useTaskRunStore((s) => s.busy[card.key] === true)
  const recording = useMeetingStore((s) => s.recording)
  const elapsed = useMeetingStore((s) => s.elapsed)
  const connecting = useMeetingStore((s) => s.connecting)

  // 占位卡：正常显示（点击提示建设中）或 soon 置灰
  if (card.kind === 'placeholder') {
    if (card.key === 'reserved-slot') {
      return (
        <article className="cp-card kind-reserved">
          <h3>敬请期待</h3>
          <div className="meta"><span className="ph-hint">预留入口</span></div>
        </article>
      )
    }
    return (
      <article
        className={`cp-card kind-placeholder${card.soon ? ' soon' : ''}`}
        onClick={editing ? undefined : () => toast(card.soon ? '二期建设中，敬请期待' : '功能建设中，敬请期待')}
      >
        <h3>{card.name}</h3>
        {card.subtitle ? <p className="cp-sub">{card.subtitle}</p> : null}
        <div className="meta">
          <span className="ph-hint">{card.soon ? '二期建设中' : '建设中'}</span>
        </div>
      </article>
    )
  }

  if (card.kind === 'link') {
    return (
      <article
        className="cp-card kind-link"
        onClick={editing ? undefined : () => void onOpenLink(card)}
      >
        <h3>{card.name}</h3>
        {card.subtitle ? <p className="cp-sub">{card.subtitle}</p> : null}
        <div className="meta">
          <span className="go">打开 →</span>
        </div>
      </article>
    )
  }

  if (card.kind === 'graph') {
    return <GraphCard cardKey={card.key} />
  }

  if (card.kind === 'meeting') {
    return (
      <article className="cp-card kind-meeting">
        <h3>{card.name}</h3>
        {card.subtitle ? <p className="cp-sub">{card.subtitle}</p> : null}
        <div className={`mt-live ${recording ? 'on' : ''}`}>
          {recording ? (
            <>
              <span className="dot" /> 录音中 {fmtDur(elapsed)}
            </>
          ) : connecting ? (
            '检测中…'
          ) : (
            '未在录音'
          )}
        </div>
        <div className="meta">
          {recording ? (
            <button className="btn-mini stop" onClick={() => useMeetingStore.getState().stop()}>
              停止
            </button>
          ) : (
            <button
              className="btn-mini"
              disabled={connecting || editing || !canMeeting}
              title={canMeeting ? undefined : '未开通实时会议权限'}
              onClick={() => void useMeetingStore.getState().start()}
            >
              开始
            </button>
          )}
          <button
            className="btn-mini ghost"
            disabled={editing || !canMeeting}
            title={canMeeting ? undefined : '未开通实时会议权限'}
            onClick={() => setMeetingOpen(true)}
          >
            进入
          </button>
        </div>
      </article>
    )
  }

  // task 卡：开始后台执行，运行态/终态实时显示（页面切换不影响）
  return (
    <article className="cp-card kind-task">
      <h3>{card.name}</h3>
      {card.subtitle ? <p className="cp-sub">{card.subtitle}</p> : null}
      <div className={`tk-state ${run?.status ?? ''}`}>
        {busy
          ? '运行中…'
          : run?.status === 'success'
            ? '已完成'
            : run?.status === 'failed'
              ? '未完成'
              : '待执行'}
      </div>
      <div className="meta">
        <button
          className="btn-mini"
          disabled={busy || editing || !canSkill}
          title={canSkill ? undefined : '未开通技能执行权限'}
          onClick={() => void startRun(card)}
        >
          {busy ? '运行中…' : run ? '再次开始' : '开始'}
        </button>
      </div>
    </article>
  )
}

export default function CockpitHome() {
  const toast = useUiStore((s) => s.toast)
  const user = useAuthStore((s) => s.user)
  const canEdit = (user?.permissions ?? []).includes('xinhere.cockpit.manage')
  const editing = useUiStore((s) => s.cockpitEditing && canEdit)
  const setCockpitEditing = useUiStore((s) => s.setCockpitEditing)
  const [groups, setGroups] = useState<CockpitCardGroup[]>([])
  const [addGroup, setAddGroup] = useState<string | null>(null) // 编辑模式：待添加卡片的分组
  const [dragKey, setDragKey] = useState<string | null>(null) // 编辑模式：拖拽排序中的卡片
  const [overKey, setOverKey] = useState<string | null>(null) // 编辑模式：拖拽悬停目标卡
  const [tplOpen, setTplOpen] = useState(false) // 信息填报：Excel 模板创建与派发

  const loadCards = () => {
    api<{ items: CockpitCardGroup[] }>('GET', '/cockpit/cards')
      .then((r) => setGroups(r.items ?? []))
      .catch(() => {})
  }

  useEffect(() => {
    loadCards()
  }, [])

  const openLink = async (card: CockpitCardItem) => {
    try {
      const r = await api<{ url: string; first_login: boolean }>('POST', '/xuanpu/cards/open', { key: card.key })
      if (r.first_login) toast('首次访问需在 XuanPu 登录一次')
      window.open(r.url, '_blank')
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
  }

  const removeCard = async (card: CockpitCardItem) => {
    if (!window.confirm(`删除卡片「${card.name}」？`)) return
    try {
      await api('DELETE', `/cockpit/cards/${card.id}`)
      toast('已删除')
      loadCards()
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
  }

  // 拖拽落点：把拖拽卡移动到目标卡位置（仅组内），重排 sort 后落库（乐观更新，失败回滚重载）
  const dropOn = (groupKey: string, targetKey: string) => {
    if (!dragKey || dragKey === targetKey) {
      setDragKey(null)
      setOverKey(null)
      return
    }
    const src = groups.find((g) => g.key === groupKey)
    if (!src) return
    const from = src.cards.findIndex((c) => c.key === dragKey)
    const to = src.cards.findIndex((c) => c.key === targetKey)
    if (from < 0 || to < 0 || from === to) {
      setDragKey(null)
      setOverKey(null)
      return
    }
    const cards = [...src.cards]
    const [moved] = cards.splice(from, 1)
    cards.splice(to, 0, moved)
    const next = cards.map((c, i) => ({ ...c, sort: (i + 1) * 10 }))
    setGroups((cur) => cur.map((g) => (g.key === groupKey ? { ...g, cards: next } : g)))
    setDragKey(null)
    setOverKey(null)
    const changed = next.filter((c, i) => c.sort !== src.cards[i].sort)
    if (changed.length === 0) return
    void Promise.all(
      changed.map((c) => api('PATCH', `/cockpit/cards/${c.id}`, { sort: c.sort })),
    )
      .then(() => toast('排序已保存'))
      .catch(() => {
        toast('排序保存失败，已恢复', 'err')
        loadCards()
      })
  }

  const groupOpts = groups.map((g) => ({ key: g.key, name: g.name }))

  return (
    <div className={`cp-wrap${editing ? ' editing' : ''}`}>
      <button
        className="cp-fill-entry"
        onClick={() => setTplOpen(true)}
        title="上传 Excel 生成填报模板并派发"
      >
        信息填报
      </button>
      {tplOpen && (
        <ScmsProvider>
          <TemplateCreateModal onClose={() => setTplOpen(false)} />
        </ScmsProvider>
      )}
      <section>
        {editing && (
          <div className="cp-toolbar">
            <span className="cp-edit-hint">编辑模式：拖拽卡片排序，点击 × 删除，点击虚线卡添加</span>
            <button className="btn-primary" onClick={() => setCockpitEditing(false)}>
              完成
            </button>
          </div>
        )}
        {groups.map((g) => (
          <div className="cp-group" key={g.key}>
            <h4 className="cp-sec-title">{g.name}</h4>
            <div className="cp-cards">
              {g.cards.map((card) =>
                editing ? (
                  <div
                    className={`cp-cell${dragKey === card.key ? ' dragging' : ''}${
                      overKey === card.key && dragKey !== card.key ? ' drag-over' : ''
                    }`}
                    key={card.key}
                    draggable
                    onDragStart={(e) => {
                      setDragKey(card.key)
                      e.dataTransfer.effectAllowed = 'move'
                      e.dataTransfer.setData('text/plain', card.key)
                    }}
                    onDragOver={(e) => {
                      if (!dragKey || dragKey === card.key) return
                      e.preventDefault()
                      e.dataTransfer.dropEffect = 'move'
                      setOverKey(card.key)
                    }}
                    onDrop={(e) => {
                      e.preventDefault()
                      dropOn(g.key, card.key)
                    }}
                    onDragEnd={() => {
                      setDragKey(null)
                      setOverKey(null)
                    }}
                  >
                    <CardView card={card} editing onOpenLink={(c) => void openLink(c)} />
                    <button
                      className="cp-del"
                      title="删除卡片"
                      onClick={() => void removeCard(card)}
                    >
                      ×
                    </button>
                  </div>
                ) : (
                  <CardView key={card.key} card={card} onOpenLink={(c) => void openLink(c)} />
                ),
              )}
              {editing && (
                <button className="cp-card kind-add" onClick={() => setAddGroup(g.key)}>
                  <h3>＋ 添加卡片</h3>
                  <div className="meta"><span className="ph-hint">添加到「{g.name}」</span></div>
                </button>
              )}
            </div>
          </div>
        ))}
        {groups.length === 0 && <div className="cp-empty">暂无可用能力</div>}
      </section>
      {addGroup !== null && (
        <CockpitCardModal
          groups={groupOpts}
          defaultGroup={addGroup}
          onClose={() => setAddGroup(null)}
          onSaved={loadCards}
        />
      )}
    </div>
  )
}
