// Xin台层中部（日·守）：业务能力（XuanPu 技能直跑）+ 业务系统（免登入口）
// 技能与入口均来自服务端：/xuanpu/skills（MCP 网关代理）与 /cockpit/entries
import { useEffect, useState } from 'react'
import { api } from '@/transport/api'
import { useUiStore } from '@/state/uiStore'
import type { CockpitEntry, XuanPuSkill } from '@/types'

export default function CockpitHome() {
  const openRun = useUiStore((s) => s.openRun)
  const toast = useUiStore((s) => s.toast)
  const [skills, setSkills] = useState<XuanPuSkill[]>([])
  const [entries, setEntries] = useState<CockpitEntry[]>([])

  useEffect(() => {
    // 技能列表经后端代理 MCP 网关；平台不可达时静默（本区显示空态）
    api<{ items: XuanPuSkill[] | { raw?: string } }>('GET', '/xuanpu/skills')
      .then((r) =>
        setSkills(Array.isArray(r.items) ? r.items.filter((s) => s.is_active !== false) : []),
      )
      .catch(() => {})
    api<{ items: CockpitEntry[] }>('GET', '/cockpit/entries')
      .then((r) => setEntries(r.items ?? []))
      .catch(() => {})
  }, [])

  // 系统卡：换一次性免登地址后新开（未绑统一身份时返回直连地址，首次需在 XuanPu 登录）
  const launch = async (key: string) => {
    try {
      const r = await api<{ url: string; first_login: boolean }>('POST', '/xuanpu/launch', { key })
      if (r.first_login) toast('首次访问需在 XuanPu 登录一次')
      window.open(r.url, '_blank')
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
  }

  return (
    <div className="cp-wrap">
      <section>
        <div>
          <h4 className="cp-sec-title">业务能力</h4>
          <div className="skills">
            {skills.map((sk, i) => (
              <article
                className={`skill ${i === 0 ? 'primary' : ''}`}
                key={sk.id}
                onClick={() => openRun({ skillId: sk.id, name: sk.name, desc: sk.description || '' })}
              >
                <div className="src">{sk.category || 'XuanPu 技能'}</div>
                <h3>{sk.name}</h3>
                <div className="desc">{sk.description}</div>
                <div className="meta">
                  <span className="go">运行 →</span>
                </div>
              </article>
            ))}
            {skills.length === 0 && <div className="cp-empty">暂无可用技能</div>}
          </div>
        </div>
        {entries.length > 0 && (
          <div className="sys-entry">
            <h5>业务系统</h5>
            <div className="sys-cards">
              {entries.map((s) => (
                <a className="sys-card" key={s.key} onClick={() => void launch(s.key)}>
                  <h3>{s.name}</h3>
                  <span className="go">→</span>
                </a>
              ))}
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
