// Xin台任务卡 · 技能后台执行单例：store 持有运行态，卡片/页面卸载不影响执行
// 每次执行落一条历史任务（POST /tasks/exec），终态 PATCH 回写输出摘要
import { create } from 'zustand'
import { api } from '@/transport/api'
import type { CockpitCardItem, HeroCardItem, TaskRecordItem, XuanPuSkill, XuanPuSkillRunResult } from '@/types'
import { useUiStore } from './uiStore'

export interface TaskRun {
  id: string // 本次运行 id（前端生成）
  cardKey: string // 来源卡片 key
  name: string // 展示名（卡片名）
  skillId: number
  status: 'running' | 'success' | 'failed'
  startedAt: number
  endedAt: number | null
  result: XuanPuSkillRunResult | null
  error: string | null
  recordId: string | null // 历史任务记录 id
}

interface TaskRunState {
  runs: TaskRun[] // 最新在前
  busy: Record<string, boolean> // 卡片 key → 有进行中的运行
  startRun: (card: CockpitCardItem | HeroCardItem) => Promise<void>
  lastRunOf: (cardKey: string) => TaskRun | null
}

let runSeq = 0
let skillsCache: XuanPuSkill[] | null = null

async function loadSkills(): Promise<XuanPuSkill[]> {
  if (skillsCache) return skillsCache
  try {
    const r = await api<{ items: XuanPuSkill[] | { raw?: string } }>('GET', '/xuanpu/skills')
    skillsCache = Array.isArray(r.items) ? (r.items as XuanPuSkill[]) : []
  } catch {
    skillsCache = []
  }
  return skillsCache
}

/** 技能解析：优先卡片配置的 skill_id；否则按 skill_name 精确 → 包含回退匹配 */
async function resolveSkill(card: CockpitCardItem | HeroCardItem): Promise<XuanPuSkill | null> {
  if (card.skill_id != null) {
    const skills = await loadSkills()
    return skills.find((s) => s.id === card.skill_id) ?? { id: card.skill_id, name: card.skill_name || card.name }
  }
  if (!card.skill_name) return null
  const skills = await loadSkills()
  return (
    skills.find((s) => s.name === card.skill_name) ??
    skills.find((s) => s.name.includes(card.skill_name!) || card.skill_name!.includes(s.name)) ??
    null
  )
}

function outText(result: XuanPuSkillRunResult): string {
  const out = result.output_data
  if (out == null) return ''
  return typeof out === 'string' ? out : JSON.stringify(out, null, 2)
}

export const useTaskRunStore = create<TaskRunState>((set, get) => ({
  runs: [],
  busy: {},

  lastRunOf(cardKey) {
    return get().runs.find((r) => r.cardKey === cardKey) ?? null
  },

  async startRun(card) {
    const key = 'key' in card ? card.key : card.id
    if (get().busy[key]) return
    const skill = await resolveSkill(card)
    if (!skill) {
      useUiStore.getState().toast(`「${card.name}」未绑定可用技能`, 'err')
      return
    }
    const run: TaskRun = {
      id: `run-${Date.now()}-${++runSeq}`,
      cardKey: key,
      name: card.name,
      skillId: skill.id,
      status: 'running',
      startedAt: Date.now(),
      endedAt: null,
      result: null,
      error: null,
      recordId: null,
    }
    set((s) => ({ runs: [run, ...s.runs], busy: { ...s.busy, [key]: true } }))

    // 落历史任务记录（失败不影响执行）
    void api<TaskRecordItem>('POST', '/tasks/exec', {
      title: card.name,
      status: 'running',
      ref_id: key,
      detail: { skill: skill.name },
    })
      .then((rec) => set((s) => ({ runs: s.runs.map((r) => (r.id === run.id ? { ...r, recordId: rec.id } : r)) })))
      .catch(() => {})

    try {
      const result = await api<XuanPuSkillRunResult>('POST', `/xuanpu/skills/${skill.id}/run`, { inputs: {} })
      const ok = !result.raw && result.status === 'success'
      set((s) => ({
        runs: s.runs.map((r) =>
          r.id === run.id
            ? { ...r, status: ok ? 'success' : 'failed', endedAt: Date.now(), result, error: result.raw || result.error || null }
            : r,
        ),
      }))
      const detail: Record<string, unknown> = {
        skill: skill.name,
        started_at: new Date(run.startedAt).toISOString(),
        ended_at: new Date().toISOString(),
      }
      if (ok) detail.output = outText(result).slice(0, 4000)
      else detail.error = result.raw || result.error || result.note || '运行未完成'
      if (run.recordId) {
        void api('PATCH', `/tasks/${run.recordId}`, { status: ok ? 'success' : 'failed', detail }).catch(() => {})
      }
      useUiStore.getState().toast(ok ? `「${card.name}」已完成` : `「${card.name}」运行未完成`, ok ? 'info' : 'err')
    } catch (e: unknown) {
      const err = e instanceof Error ? e.message : String(e)
      set((s) => ({
        runs: s.runs.map((r) => (r.id === run.id ? { ...r, status: 'failed', endedAt: Date.now(), error: err } : r)),
      }))
      if (run.recordId) {
        void api('PATCH', `/tasks/${run.recordId}`, { status: 'failed', detail: { skill: skill.name, error: err } }).catch(() => {})
      }
      useUiStore.getState().toast(`「${card.name}」运行失败：${err}`, 'err')
    } finally {
      set((s) => ({ busy: { ...s.busy, [key]: false } }))
    }
  },
}))

/** 技能列表刷新（管理端改技能后可调用；暂无入口，保留能力） */
export function invalidateSkills() {
  skillsCache = null
}
