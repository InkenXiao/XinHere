// Xin台主界面（日·守）：分组卡片（AI工作台 / AI技能 / 业务系统），全部来自服务端配置
// link 卡=页面跳转（免登）；task 卡=后台执行技能；meeting 卡=实时会议（录音后台运行）
import { useEffect, useState } from 'react'
import { api } from '@/transport/api'
import { useUiStore } from '@/state/uiStore'
import { useMeetingStore } from '@/state/meetingStore'
import { useTaskRunStore } from '@/state/taskRunStore'
import type { CockpitCardGroup, CockpitCardItem } from '@/types'

function fmtDur(sec: number) {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function CardView({ card, onOpenLink }: { card: CockpitCardItem; onOpenLink: (c: CockpitCardItem) => void }) {
  const setMeetingOpen = useUiStore((s) => s.setMeetingOpen)
  const startRun = useTaskRunStore((s) => s.startRun)
  const run = useTaskRunStore((s) => s.runs.find((r) => r.cardKey === card.key) ?? null)
  const busy = useTaskRunStore((s) => s.busy[card.key] === true)
  const recording = useMeetingStore((s) => s.recording)
  const elapsed = useMeetingStore((s) => s.elapsed)

  if (card.kind === 'link') {
    return (
      <article className="cp-card kind-link" onClick={() => void onOpenLink(card)}>
        <h3>{card.name}</h3>
        <div className="meta">
          <span className="go">打开 →</span>
        </div>
      </article>
    )
  }

  if (card.kind === 'meeting') {
    return (
      <article className="cp-card kind-meeting">
        <h3>{card.name}</h3>
        <div className={`mt-live ${recording ? 'on' : ''}`}>
          {recording ? (
            <>
              <span className="dot" /> 录音中 {fmtDur(elapsed)}
            </>
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
            <button className="btn-mini" onClick={() => void useMeetingStore.getState().start()}>
              开始
            </button>
          )}
          <button className="btn-mini ghost" onClick={() => setMeetingOpen(true)}>
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
        <button className="btn-mini" disabled={busy} onClick={() => void startRun(card)}>
          {busy ? '运行中…' : run ? '再次开始' : '开始'}
        </button>
      </div>
    </article>
  )
}

export default function CockpitHome() {
  const toast = useUiStore((s) => s.toast)
  const [groups, setGroups] = useState<CockpitCardGroup[]>([])

  useEffect(() => {
    api<{ items: CockpitCardGroup[] }>('GET', '/cockpit/cards')
      .then((r) => setGroups(r.items ?? []))
      .catch(() => {})
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

  return (
    <div className="cp-wrap">
      <section>
        {groups.map((g) => (
          <div className="cp-group" key={g.key}>
            <h4 className="cp-sec-title">{g.name}</h4>
            <div className="cp-cards">
              {g.cards.map((card) => (
                <CardView key={card.key} card={card} onOpenLink={(c) => void openLink(c)} />
              ))}
            </div>
          </div>
        ))}
        {groups.length === 0 && <div className="cp-empty">暂无可用能力</div>}
      </section>
    </div>
  )
}
