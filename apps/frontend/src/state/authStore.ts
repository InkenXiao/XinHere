// 认证：token(localStorage 持久化) + user；login/logout/me
import { create } from 'zustand'
import { api, getToken, setToken, onUnauthorized } from '@/transport/api'
import type { UserInfo } from '@/types'

interface AuthState {
  token: string | null
  user: UserInfo | null
  ready: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  fetchMe: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set, get) => ({
  token: getToken(),
  user: null,
  ready: false,
  async login(username, password) {
    const r = await api<{ token: string; user: UserInfo }>('POST', '/auth/login', { username, password })
    setToken(r.token)
    set({ token: r.token, user: r.user })
  },
  async logout() {
    try {
      await api('POST', '/auth/logout')
    } catch {
      /* 忽略登出请求失败 */
    }
    setToken(null)
    set({ token: null, user: null })
  },
  async fetchMe() {
    if (!get().token) {
      set({ ready: true })
      return
    }
    try {
      const user = await api<UserInfo>('GET', '/auth/me')
      set({ user, ready: true })
    } catch {
      set({ ready: true })
    }
  },
}))

// 401 统一登出
onUnauthorized(() => {
  setToken(null)
  useAuthStore.setState({ token: null, user: null })
})
