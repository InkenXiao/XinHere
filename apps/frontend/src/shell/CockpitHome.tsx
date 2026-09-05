// 驾驶舱层中部（日·守）：业务能力卡 + 业务系统入口
// 技能卡点击 = 复用 HeroHome 的 enterChat 路径（新会话 → 瞭望塔会话视图 → 发送意图）
import { runtimeEnv } from '@/config'
import { useSessionStore } from '@/state/sessionStore'
import { useUiStore } from '@/state/uiStore'

// 子系统跳转（运行时配置 window.__ENV__，空值不渲染）
const SUB_SYSTEMS = [
  { name: '运营管理系统', url: runtimeEnv.OPS_URL || undefined },
  { name: '青山知识库', url: runtimeEnv.KB_URL || undefined },
  { name: 'CoWork', url: runtimeEnv.COWORK_URL || undefined },
].filter((s) => s.url)

const SKILLS = [
  { primary: true, title: '投后管理报告', desc: '生成、查看被投企业的投后管理报告', prompt: '生成投后报告' },
  { primary: false, title: '信息填报', desc: '指定企业与指标，自动汇总填报进度并发送催收提醒', prompt: '发起风险填报' },
  { primary: false, title: '经营考核任务下发', desc: '按季度模板向被投企业下发经营考核任务，自动跟踪查收与反馈状态', prompt: '任务执行统计' },
]

export default function CockpitHome() {
  const runSkill = async (prompt: string) => {
    const s = useSessionStore.getState()
    if (s.sending) return
    await s.newSession()
    const ui = useUiStore.getState()
    ui.setWorkView('chat')
    ui.setMode('tower')
    void s.send(prompt)
  }

  return (
    <div className="cp-wrap">
      <section>
        <div>
          <h4 className="cp-sec-title">业务能力</h4>
          <div className="skills">
            {SKILLS.map((sk) => (
              <article
                className={`skill ${sk.primary ? 'primary' : ''}`}
                key={sk.title}
                onClick={() => void runSkill(sk.prompt)}
              >
                <div className="src">业务系统 1.0</div>
                <h3>{sk.title}</h3>
                <div className="desc">{sk.desc}</div>
                <div className="meta">
                  <span className="go">进入 →</span>
                </div>
              </article>
            ))}
          </div>
        </div>
        {SUB_SYSTEMS.length > 0 && (
          <div className="sys-entry">
            <h5>业务系统</h5>
            <div className="sys-cards">
              {SUB_SYSTEMS.map((s) => (
                <a className="sys-card" key={s.name} href={s.url} target="_blank" rel="noreferrer">
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
