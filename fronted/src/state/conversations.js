import { ref } from 'vue'

function read(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback } catch { return fallback }
}

let ownerId = null
export const conversations = ref([])
export const activeSessionId = ref(`sess-${crypto.randomUUID()}`)
export const conversationBusy = ref(false)

export function setConversationUser(userId) {
  if (ownerId === userId) return
  ownerId = userId
  conversationBusy.value = false
  const stored = userId ? read(`kb_conversations:${userId}`, []) : []
  conversations.value = Array.isArray(stored) ? stored.filter(item => item && typeof item.id === 'string' && typeof item.title === 'string') : []
  let previous
  try { previous = userId ? localStorage.getItem(`kb_session_id:${userId}`) : null } catch { /* 使用内存会话。 */ }
  activeSessionId.value = previous || `sess-${crypto.randomUUID()}`
  persist()
}

function persist() {
  if (!ownerId) return
  try {
    localStorage.setItem(`kb_session_id:${ownerId}`, activeSessionId.value)
    localStorage.setItem(`kb_conversations:${ownerId}`, JSON.stringify(conversations.value))
  } catch { /* 浏览器禁用存储时，仍可在当前页面使用。 */ }
}

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
