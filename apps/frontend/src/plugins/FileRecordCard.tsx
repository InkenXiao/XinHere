// file-record-card@1：file/record 事件 → 文件产出卡片（.docx/.pptx）
// 点击打开文件编辑页 URL（现阶段后端给 /demo.html，后续由 skill 服务动态生成真实地址）
import type { ComponentDefinition, ComponentProps } from '@/registry/types'
import type { PlatformEvent } from '@/types'

interface S {
  file_id: string
  name: string
  file_type: 'docx' | 'pptx'
  url: string
  skill_key: string
}

function match(evt: PlatformEvent) {
  if (evt.type === 'file/record') return { id: String(evt.data.file_id), role: 'start' as const }
  return null
}

function reduce(_s: S | undefined, evt: PlatformEvent): S {
  return {
    file_id: String(evt.data.file_id ?? ''),
    name: String(evt.data.name ?? '未命名文件'),
    file_type: evt.data.file_type === 'pptx' ? 'pptx' : 'docx',
    url: String(evt.data.url ?? ''),
    skill_key: String(evt.data.skill_key ?? ''),
  }
}

const TYPE_LABEL: Record<S['file_type'], string> = { docx: 'Word 文档', pptx: 'PPT 演示文稿' }
const TYPE_ICON: Record<S['file_type'], string> = { docx: '📄', pptx: '📊' }

function Comp({ state }: ComponentProps<S>) {
  if (!state) return null
  const open = () => {
    if (!state.url) return
    window.open(state.url, '_blank', 'noopener')
  }
  return (
    <div className="file-card" onClick={open} role="button" title="点击打开文件">
      <span className={`fc-ico ${state.file_type}`}>{TYPE_ICON[state.file_type]}</span>
      <div className="fc-main">
        <div className="fc-name">{state.name}</div>
        <div className="fc-sub">{TYPE_LABEL[state.file_type]} · 点击查看</div>
      </div>
      <span className={`fc-type ${state.file_type}`}>.{state.file_type}</span>
    </div>
  )
}

const def: ComponentDefinition<S> = { kind: 'file-record-card', version: 1, match, reduce, component: Comp }
export default def
