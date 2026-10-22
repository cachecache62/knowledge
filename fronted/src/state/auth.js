import { ref } from 'vue'
import { AUTH_API_BASE, requestJson } from '../api.js'
import { setConversationUser } from './conversations.js'

export const currentUser = ref(null)
export const authChecked = ref(false)
export const authError = ref('')
let checking
let revision = 0

function applyUser(user) {
  if (user !== null && (!user || !user.id || typeof user.email !== 'string')) throw new Error('账户服务未返回有效的用户信息')
  if (currentUser.value?.id !== user?.id) setConversationUser(user?.id ?? null)
  currentUser.value = user
  authChecked.value = true
}

export function clearAuthentication() {
  revision++
  applyUser(null)
}

export async function checkSession() {
  if (checking) return checking
  const version = revision
  checking = (async () => {
    try {
      const result = await requestJson(AUTH_API_BASE, '/auth/me', { signal: AbortSignal.timeout(8000) })
      if (version === revision) {
        applyUser(result.user)
        authError.value = ''
      }
    } catch (error) {
      if (version === revision) {
        applyUser(null)
        authError.value = error.status === 401 ? '' : '暂时无法验证登录状态，请确认账户服务已启动后重试。'
      }
    } finally {
      checking = undefined
    }
    return currentUser.value
  })()
  return checking
}

function notifyOtherTabs() {
  try { localStorage.setItem('kb_auth_changed', `${Date.now()}-${crypto.randomUUID()}`) } catch { /* 不依赖本地存储维护登录凭据。 */ }
}

export async function authenticate(mode, payload, signal) {
  const result = await requestJson(AUTH_API_BASE, `/auth/${mode}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000),
  })
  if (signal?.aborted) return
  revision++
  applyUser(result.user)
  authError.value = ''
  notifyOtherTabs()
}

export async function signOut() {
  await requestJson(AUTH_API_BASE, '/auth/logout', { method: 'POST', signal: AbortSignal.timeout(8000) })
  clearAuthentication()
  authError.value = ''
  notifyOtherTabs()
}
