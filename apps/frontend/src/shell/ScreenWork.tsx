// Xin语层中部：hero（居中大问数框）↔ 会话（全量对话容器）双视图
import { useUiStore } from '@/state/uiStore'
import ChatPanel from './ChatPanel'
import HeroHome from './HeroHome'
import RadarInstrument from './RadarInstrument'

export default function ScreenWork() {
  const workView = useUiStore((s) => s.workView)

  return (
    <div className="tw-wrap">
      {workView === 'hero' ? (
        <HeroHome />
      ) : (
        <div className="tw-chat-live">
          <ChatPanel />
        </div>
      )}
      {workView === 'hero' && <RadarInstrument />}
    </div>
  )
}
