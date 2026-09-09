// Xin语页面雷达图：参照设计稿 instrument —— 双层轨道 + 扫描扇区 + 十字准线 + 四象限消息信号（悬停出提示）
// 纯展示组件，不接入业务数据，不影响原有功能逻辑
import { useState } from 'react'

type Priority = 'important-urgent' | 'important-not-urgent' | 'urgent-not-important' | 'neither'

interface RadarDot {
  id: string
  priority: Priority
  title: string
  source: string
  time: string
  summary: string
  pos: [number, number] // left/top 百分比
}

const PRIORITY_META: Record<Priority, { label: string; rgb: string }> = {
  'important-urgent': { label: '·', rgb: '255, 198, 112' },
  'important-not-urgent': { label: '·', rgb: '150, 226, 255' },
  'urgent-not-important': { label: '·', rgb: '118, 243, 210' },
  neither: { label: '·', rgb: '168, 187, 205' },
}

const DOTS: RadarDot[] = [
  { id: 'risk-alert', priority: 'important-urgent', title: '重点企业风险预警', source: '风控系统', time: '2 分钟前', summary: '经营现金流出现异常波动，建议立即核查。', pos: [70, 29] },
  { id: 'annual-plan', priority: 'important-not-urgent', title: '年度经营计划待确认', source: '投后管理', time: '18 分钟前', summary: '3 家企业已提交年度计划，等待审核确认。', pos: [30, 28] },
  { id: 'data-deadline', priority: 'urgent-not-important', title: '数据填报即将截止', source: '信息填报', time: '32 分钟前', summary: '本期填报将在今日 18:00 截止，请及时跟进。', pos: [70, 68] },
  { id: 'weekly-news', priority: 'neither', title: '行业资讯周报已生成', source: '研究中心', time: '1 小时前', summary: '本周行业动态与政策速览已整理完毕。', pos: [30, 70] },
  { id: 'weather-alert', priority: 'important-urgent', title: '极端天气影响提醒', source: '农情监测', time: '新消息', summary: '未来 48 小时持续降雨，请注意防范。', pos: [80, 38] },
]

const QUADRANT_COUNTS: Record<Priority, number> = {
  'important-urgent': 2,
  'important-not-urgent': 1,
  'urgent-not-important': 1,
  neither: 1,
}

export default function RadarInstrument() {
  const [hover, setHover] = useState<RadarDot | null>(null)

  return (
    <aside className="tw-radar" aria-label="消息优先级雷达">
      <div className="radar-orbit radar-orbit--outer" />
      <div className="radar-orbit radar-orbit--inner" />
      <div className="radar-sweep" />
      <div className="radar-crosshair" />
      <div className="radar-quadrants" aria-hidden="true">
        <span className="radar-q radar-q--inu">
          · <b>{String(QUADRANT_COUNTS['important-not-urgent']).padStart(2, '0')}</b>
        </span>
        <span className="radar-q radar-q--iu">
          · <b>{String(QUADRANT_COUNTS['important-urgent']).padStart(2, '0')}</b>
        </span>
        <span className="radar-q radar-q--nei">
          · <b>{String(QUADRANT_COUNTS.neither).padStart(2, '0')}</b>
        </span>
        <span className="radar-q radar-q--uni">
          · <b>{String(QUADRANT_COUNTS['urgent-not-important']).padStart(2, '0')}</b>
        </span>
      </div>
      <div className="radar-dots">
        {DOTS.map((d) => (
          <button
            type="button"
            key={d.id + d.pos.join()}
            className={`radar-dot radar-dot--${d.priority}`}
            style={{ left: `${d.pos[0]}%`, top: `${d.pos[1]}%` }}
            aria-label={`${PRIORITY_META[d.priority].label}：${d.title}`}
            onMouseEnter={() => setHover(d)}
            onMouseLeave={() => setHover(null)}
            onFocus={() => setHover(d)}
            onBlur={() => setHover(null)}
            tabIndex={-1}
          >
            <span className="radar-dot__core" />
          </button>
        ))}
      </div>
      {hover && (
        <div className="radar-tip" style={{ ['--tip-rgb' as string]: PRIORITY_META[hover.priority].rgb }}>
          <span style={{ color: `rgb(${PRIORITY_META[hover.priority].rgb})` }}>{PRIORITY_META[hover.priority].label}</span>
          <strong>{hover.title}</strong>
          <small>
            {hover.source} · {hover.time}
          </small>
          <p>{hover.summary}</p>
        </div>
      )}
      <div className="radar-signal">
        <span />
        <div>
          <strong></strong>
          <small></small>
        </div>
      </div>
      <span className="radar-axis radar-axis--n"> </span>
      <span className="radar-axis radar-axis--e"> </span>
      <span className="radar-axis radar-axis--s"> </span>
      <span className="radar-axis radar-axis--w"> </span>
    </aside>
  )
}
