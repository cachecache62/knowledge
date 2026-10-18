import { ref } from 'vue'

function read(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback } catch { return fallback }
}

function previousSession() {
  try { return localStorage.getItem('kb_session_id') } catch { return null }
}

const previous = previousSession()
const stored = read('kb_conversations', null)
export const conversations = ref(Array.isArray(stored) ? stored.filter(item => item && typeof item.id === 'string' && typeof item.title === 'string') : [])
export const activeSessionId = ref(previous || `sess-${crypto.randomUUID()}`)
export const conversationBusy = ref(false)

function persist() {
  try {
    localStorage.setItem('kb_session_id', activeSessionId.value)
    localStorage.setItem('kb_conversations', JSON.stringify(conversations.value))
  } catch { /* 浏览器禁用存储时，仍可在当前页面使用。 */ }
}

if (previous && stored === null) {
  conversations.value.unshift({ id: previous, title: '上次的对话', updatedAt: Date.now() })
}
persist()

export function selectConversation(id) {
  if (conversationBusy.value || id === activeSessionId.value) return
  activeSessionId.value = id
  persist()
}

export function newConversation() {
  if (conversationBusy.value) return
  activeSessionId.value = `sess-${crypto.randomUUID()}`
  persist()
}

export function rememberConversation(text, id = activeSessionId.value) {
  const existing = conversations.value.find(item => item.id === id)
  const entry = { id, title: existing && existing.title !== '上次的对话' ? existing.title : text.replace(/\s+/g, ' ').trim().slice(0, 36), updatedAt: Date.now() }
  conversations.value = [entry, ...conversations.value.filter(item => item.id !== id)]
  persist()
}

export function forgetConversation() {
  conversations.value = conversations.value.filter(item => item.id !== activeSessionId.value)
  persist()
}
