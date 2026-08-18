// 运行时配置：优先读 nginx 渲染注入的 window.__ENV__（生产改配置仅重启容器即生效，
// 无需重新构建 dist / 重建镜像），回退到构建期 VITE_* 环境变量，再回退默认值。
// 渲染源见 deploy/config.js.template 与 deploy/docker-entrypoint.d/30-render-env.sh。
type RuntimeEnv = {
  API_BASE?: string
  OPS_URL?: string
  KB_URL?: string
  COWORK_URL?: string
  MODEL_NAME?: string
  MOCK?: string
}

declare global {
  interface Window {
    __ENV__?: RuntimeEnv
  }
}

// 空串视为未配置（nginx envsubst 对未设置变量输出空串）
const pick = (v: string | undefined, fb: string | undefined): string => {
  const first = v && v.trim() ? v.trim() : ''
  const second = fb && fb.trim() ? fb.trim() : ''
  return first || second
}

const rt: RuntimeEnv = (typeof window !== 'undefined' ? window.__ENV__ : undefined) ?? {}

export const runtimeEnv: RuntimeEnv = {
  API_BASE: pick(rt.API_BASE, (import.meta.env.VITE_API_BASE as string | undefined) || '/api/v1'),
  OPS_URL: pick(rt.OPS_URL, import.meta.env.VITE_OPS_URL as string | undefined),
  KB_URL: pick(rt.KB_URL, import.meta.env.VITE_KB_URL as string | undefined),
  COWORK_URL: pick(rt.COWORK_URL, import.meta.env.VITE_COWORK_URL as string | undefined),
  MODEL_NAME: pick(rt.MODEL_NAME, import.meta.env.VITE_MODEL_NAME as string | undefined),
  MOCK: pick(rt.MOCK, (import.meta.env.VITE_MOCK as string | undefined) || ''),
}
