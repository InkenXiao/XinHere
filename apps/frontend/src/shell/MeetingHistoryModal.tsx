// 会议历史（scms 管线 /meeting/history）：列表 + 详情回放（转写段落 + 纪要 Markdown）
import { useEffect, useState } from 'react'
import { api } from '@/transport/api'
import Markdown from './Markdown'

interface MeetingHistoryItem {
  meeting_id: string
  title: string
  status: string
  started_at: string | null
  ended_at: string | null
  duration_seconds?: number
  minutes_markdown?: string | null
  segment_count?: number
}

interface MeetingSegmentRow {
  seq: number
  start_sec: number
  end_sec: number
  text: string
  speaker?: string | null
}

function fmt(sec?: number | null): string {
  if (!sec) return '--:--'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export default function MeetingHistoryModal({ onClose }: { onClose: () => void }) {
  const [items, setItems] = useState<MeetingHistoryItem[]>([])
  const [err, setErr] = useState('')
  const [detail, setDetail] = useState<{ m: MeetingHistoryItem; segs: MeetingSegmentRow[] } | null>(null)

  useEffect(() => {
    api<{ items: MeetingHistoryItem[] }>('GET', '/meeting/history?limit=50')
      .then((r) => setItems(r.items ?? []))
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : '加载失败'))
  }, [])

  const openDetail = async (id: string) => {
    try {
      const r = await api<MeetingHistoryItem & { segments?: MeetingSegmentRow[] }>('GET', `/meeting/${id}`)
      setDetail({ m: r, segs: r.segments ?? [] })
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '详情加载失败')
    }
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal meeting-history" onClick={(e) => e.stopPropagation()} style={{ width: 'min(760px,94vw)' }}>
        <div className="m-head">
          <span>{detail ? `会议回放 · ${detail.m.title || '未命名会议'}` : '历史会议'}</span>
          <button className="m-close" onClick={() => (detail ? setDetail(null) : onClose())}>
            {detail ? '← 返回列表' : '×'}
          </button>
        </div>
        <div className="m-body">
          {err && <div className="form-msg err">{err}</div>}
          {!detail && !err && (items.length === 0 ? <div className="td-empty">暂无历史会议</div> : (
            <div className="mh-list">
              {items.map((m) => (
                <div key={m.meeting_id} className="mh-item" onClick={() => void openDetail(m.meeting_id)}>
                  <span className="mh-title">{m.title || '未命名会议'}</span>
                  <span className="mh-meta">
                    {m.started_at ? new Date(m.started_at).toLocaleString('zh-CN') : '--'}
                    · {fmt(m.duration_seconds ?? (m.ended_at && m.started_at
                      ? (new Date(m.ended_at).getTime() - new Date(m.started_at).getTime()) / 1000 : 0))}
                    · {m.segment_count ?? 0} 段
                  </span>
                </div>
              ))}
            </div>
          ))}
          {detail && (
            <>
              <h4 className="mh-sec">转写段落（{detail.segs.length}）</h4>
              <div className="mh-segs">
                {detail.segs.map((s) => (
                  <div key={s.seq} className="mh-seg">
                    <span className="mh-time">{fmt(s.start_sec)}</span>
                    {s.speaker && <span className="mh-speaker">{s.speaker}</span>}
                    <span>{s.text}</span>
                  </div>
                ))}
                {detail.segs.length === 0 && <div className="td-empty">无转写段落</div>}
              </div>
              {detail.m.minutes_markdown && (
                <>
                  <h4 className="mh-sec">会议纪要</h4>
                  <Markdown text={detail.m.minutes_markdown} />
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
