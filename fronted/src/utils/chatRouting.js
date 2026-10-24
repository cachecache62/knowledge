import { activeSessionId, conversationBusy, selectConversation } from '../state/conversations.js'

export function chatPath(id = activeSessionId.value) {
  return `/chat/${encodeURIComponent(id)}`
}

// 登录检查完成后同步会话，保证首次渲染和历史请求都使用地址栏中的 ID。
export function synchronizeChatRoute(to) {
  if (to.name !== 'chat') return true
  const id = to.params.sessionId
  if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(id)) {
    return { path: chatPath(), replace: true }
  }
  if (conversationBusy.value && id !== activeSessionId.value) return false
  selectConversation(id)
  return true
}
