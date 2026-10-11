// 逆回购行情提醒监控页（/repo-alerts）：后端公共流接入状态 + 接入流水日志。
// 没有提醒时靠 30s 心跳日志体现链路活着；提醒到达即浏览器系统通知（最小化也弹）。
// 入口：直接 URL（后续可挂到驾驶舱/Xin台卡片）；数据 5s 轮询（store 引用计数）。
import { useEffect } from 'react'
import { goBack } from '../hashNav'
import { Button, Table, Tag, Tooltip } from 'antd'
import { ArrowLeft, Bell, BellOff, RefreshCw } from 'lucide-react'
import {
  startRepoAlertPolling, stopRepoAlertPolling, useRepoAlertStore,
  type RepoLogItem,
} from '../store/repoAlertStore'

const KIND_TAG: Record<string, { color: string; label: string }> = {
  connect: { color: 'blue', label: '连接' },
  heartbeat: { color: 'green', label: '心跳' },
  alert: { color: 'red', label: '提醒' },
  disconnect: { color: 'orange', label: '断线' },
  error: { color: 'red', label: '异常' },
}

const STATE_TEXT: Record<string, { color: string; text: string }> = {
  online: { color: 'text-[var(--green)]', text: '在线' },
  connecting: { color: 'text-[var(--amber)]', text: '连接中' },
  disconnected: { color: 'text-[var(--red)]', text: '已断线（自动重连）' },
  stopped: { color: 'text-[var(--ink-30)]', text: '未启动' },
}

function fmtTime(iso?: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

export default function RepoAlertsPage() {
    const status = useRepoAlertStore((s) => s.status)
  const log = useRepoAlertStore((s) => s.log)
  const alerts = useRepoAlertStore((s) => s.alerts)
  const notifPermission = useRepoAlertStore((s) => s.notifPermission)
  const refresh = useRepoAlertStore((s) => s.refresh)
  const requestPermission = useRepoAlertStore((s) => s.requestNotifPermission)

  useEffect(() => {
    startRepoAlertPolling()
    return () => stopRepoAlertPolling()
  }, [])

  const state = STATE_TEXT[status?.state ?? 'stopped']
  const items: RepoLogItem[] = log

  return (
    // relative z-10 盖住 .app-bg（fixed z-0 星空图），否则背景透出导致浅色主题下文字不可读
    <div className="relative z-10 h-screen flex flex-col bg-[var(--bg)] text-[var(--ink-100)] overflow-hidden">
      <header className="flex items-center gap-3 px-6 py-4 border-b border-[var(--card-border)] bg-[var(--card-bg)] shrink-0">
        <Button type="text" icon={<ArrowLeft size={16} />} onClick={() => goBack()} />
        <div className="flex-1">
          <h1 className="text-lg font-semibold">逆回购行情提醒 · 接入监控</h1>
        </div>
        {notifPermission === 'granted' ? (
          <Tag icon={<Bell size={12} />} color="green">系统通知已开启</Tag>
        ) : notifPermission === 'unsupported' ? (
          <Tag icon={<BellOff size={12} />}>浏览器不支持通知</Tag>
        ) : (
          <Button type="primary" icon={<Bell size={14} />} onClick={() => void requestPermission()}>
            开启系统通知
          </Button>
        )}
        <Tooltip title="立即刷新">
          <Button icon={<RefreshCw size={14} />} onClick={() => void refresh()} />
        </Tooltip>
      </header>

      <div className="flex-1 overflow-y-auto p-6 space-y-6 max-w-5xl mx-auto w-full">
        {/* 状态卡片 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="rounded-lg border border-[var(--card-border)] bg-[var(--card-bg)] p-4">
            <div className="text-xs text-[var(--ink-50)]">连接状态</div>
            <div className={`mt-1 text-base font-medium ${state.color}`}>{state.text}</div>
            {status?.last_error && (
              <div className="mt-1 text-xs text-[var(--red)] break-all">{status.last_error}</div>
            )}
          </div>
          <div className="rounded-lg border border-[var(--card-border)] bg-[var(--card-bg)] p-4">
            <div className="text-xs text-[var(--ink-50)]">最近心跳</div>
            <div className="mt-1 text-base font-medium tabular-nums">{fmtTime(status?.last_heartbeat_at)}</div>
            <div className="mt-1 text-xs text-[var(--ink-30)]">累计 {status?.heartbeat_count ?? 0} 次</div>
          </div>
          <div className="rounded-lg border border-[var(--card-border)] bg-[var(--card-bg)] p-4">
            <div className="text-xs text-[var(--ink-50)]">提醒次数</div>
            <div className="mt-1 text-base font-medium tabular-nums">{status?.alert_count ?? 0}</div>
            <div className="mt-1 text-xs text-[var(--ink-30)]">最近 {fmtTime(status?.last_alert_at)}</div>
          </div>
          <div className="rounded-lg border border-[var(--card-border)] bg-[var(--card-bg)] p-4">
            <div className="text-xs text-[var(--ink-50)]">重连次数</div>
            <div className="mt-1 text-base font-medium tabular-nums">{status?.reconnect_count ?? 0}</div>
            <div className="mt-1 text-xs text-[var(--ink-30)]">接入 {fmtTime(status?.connected_at)}</div>
          </div>
        </div>

        {/* 本会话收到的提醒（SSE 实时，含系统通知） */}
        {alerts.length > 0 && (
          <section>
            <h2 className="mb-2 text-sm font-medium text-[var(--ink-70)]">本次会话收到的提醒</h2>
            <div className="space-y-2">
              {alerts.map((a, i) => (
                <div key={a.eventId || i} className="rounded-lg border border-[rgba(220,38,38,.35)] bg-[rgba(220,38,38,.10)] p-3">
                  <div className="text-sm font-medium text-[var(--red)]">{a.title || '逆回购利率提醒'}</div>
                  <div className="text-sm text-[var(--red)] opacity-80">{a.body}</div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* 接入流水日志 */}
        <section>
          <h2 className="mb-2 text-sm font-medium text-[var(--ink-70)]">接入流水（最新在前，5 秒自动刷新）</h2>
          <Table<RepoLogItem>
            size="small"
            rowKey={(r) => `${r.time}-${r.kind}-${r.message}`}
            dataSource={items}
            pagination={{ pageSize: 20, showSizeChanger: false }}
            columns={[
              {
                title: '时间', dataIndex: 'time', width: 110,
                render: (v: string) => <span className="tabular-nums text-xs">{fmtTime(v)}</span>,
              },
              {
                title: '类型', dataIndex: 'kind', width: 80,
                render: (v: string) => {
                  const k = KIND_TAG[v] ?? { color: 'default', label: v }
                  return <Tag color={k.color}>{k.label}</Tag>
                },
              },
              {
                title: '内容', dataIndex: 'message',
                render: (v: string, r) => (
                  <span className={r.kind === 'alert' ? 'text-[var(--red)] font-medium' : 'text-[var(--ink-70)]'}>{v}</span>
                ),
              },
            ]}
          />
        </section>
      </div>
    </div>
  )
}
