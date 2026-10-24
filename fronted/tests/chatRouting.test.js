import assert from 'node:assert/strict'
import test from 'node:test'
import { createMemoryHistory, createRouter, isNavigationFailure, NavigationFailureType } from 'vue-router'
import { activeSessionId, conversationBusy, newConversation, setConversationUser } from '../src/state/conversations.js'
import { chatPath, synchronizeChatRoute } from '../src/utils/chatRouting.js'

async function open(path) {
  setConversationUser(null)
  setConversationUser(1)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/chat/:sessionId?', name: 'chat', component: {} },
      { path: '/documents', component: {} },
    ],
  })
  router.beforeEach(synchronizeChatRoute)
  await router.push(path)
  await router.isReady()
  return router
}

async function travel(router, delta) {
  const finished = new Promise(resolve => {
    const remove = router.afterEach(() => { remove(); resolve() })
  })
  router.go(delta)
  await finished
}

test('直接打开或刷新对话链接时，地址中的 ID 覆盖浏览器记住的会话', async () => {
  const router = await open('/chat/sess-from-link')
  assert.equal(activeSessionId.value, 'sess-from-link')
  assert.equal(router.currentRoute.value.params.sessionId, 'sess-from-link')
  // 刷新后重新创建路由，不能依赖最近对话列表或旧的内存状态。
  await open('/chat/sess-from-link')
  assert.equal(activeSessionId.value, 'sess-from-link')
})

test('旧聊天入口自动补全当前 ID，无效 ID 回到合法会话地址', async () => {
  const router = await open('/chat')
  assert.match(router.currentRoute.value.path, /^\/chat\/sess-[0-9a-f-]{36}$/)
  assert.equal(router.currentRoute.value.path, chatPath())
  const current = activeSessionId.value
  await router.push(`/chat/${'x'.repeat(129)}`)
  assert.equal(router.currentRoute.value.path, chatPath(current))
  assert.equal(activeSessionId.value, current)
})

test('切换、新建及前进后退始终保持地址和当前对话一致', async () => {
  const router = await open('/chat/sess-first')
  await router.push(chatPath('sess-second'))
  assert.equal(activeSessionId.value, 'sess-second')
  newConversation()
  const created = activeSessionId.value
  await router.push(chatPath())
  assert.equal(router.currentRoute.value.params.sessionId, created)
  await travel(router, -1)
  assert.equal(activeSessionId.value, 'sess-second')
  await travel(router, -1)
  assert.equal(activeSessionId.value, 'sess-first')
  await travel(router, 1)
  assert.equal(activeSessionId.value, 'sess-second')
  await router.push('/documents')
  await router.push('/chat')
  assert.equal(router.currentRoute.value.path, '/chat/sess-second')
})

test('回答期间阻止通过地址切换会话，资料库及当前会话仍可访问', async () => {
  const router = await open('/chat/sess-running')
  conversationBusy.value = true
  try {
    const failure = await router.push(chatPath('sess-other'))
    assert.equal(isNavigationFailure(failure, NavigationFailureType.aborted), true)
    assert.equal(router.currentRoute.value.path, '/chat/sess-running')
    assert.equal(activeSessionId.value, 'sess-running')
    await router.push('/documents')
    assert.equal(router.currentRoute.value.path, '/documents')
    await router.push('/chat')
    assert.equal(router.currentRoute.value.path, '/chat/sess-running')
    await router.push(chatPath('sess-other'))
    assert.equal(activeSessionId.value, 'sess-running')
  } finally {
    conversationBusy.value = false
  }
  await router.push(chatPath('sess-other'))
  assert.equal(activeSessionId.value, 'sess-other')
})
