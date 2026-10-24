import assert from 'node:assert/strict'
import test from 'node:test'
import { safeDestination } from '../src/utils/authRedirect.js'

test('登录后只跳转到允许的业务路由', () => {
  assert.equal(safeDestination('/import'), '/import')
  assert.equal(safeDestination('/documents'), '/documents')
  assert.equal(safeDestination('/trash'), '/trash')
  assert.equal(safeDestination('/import-traces'), '/import-traces')
  const tracePath = '/import-traces/7633e484-3bfa-4bdc-a055-05b7c8bb3e99'
  assert.equal(safeDestination(tracePath), tracePath)
  assert.equal(safeDestination('/chat?source=login'), '/chat?source=login')
  assert.equal(safeDestination('/chat/sess-123?source=login'), '/chat/sess-123?source=login')
  for (const path of ['https://evil.example', '//evil.example', '/login', '/register', '/chat/../../login', '/chat/sess-123/../../login', '/chat/%2F%2Fevil.example', null, ['/import']]) {
    assert.equal(safeDestination(path), '/chat')
  }
})

test('最近对话按登录用户隔离，退出后内存清空且再次登录可恢复', async () => {
  const values = new Map()
  globalThis.localStorage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }
  const state = await import('../src/state/conversations.js')
  state.setConversationUser(1)
  state.rememberConversation('Account A private title')
  const firstSession = state.activeSessionId.value
  state.setConversationUser(null)
  assert.equal(state.conversations.value.length, 0)
  state.setConversationUser(2)
  assert.equal(state.conversations.value.length, 0)
  assert.notEqual(state.activeSessionId.value, firstSession)
  state.rememberConversation('Account B private title')
  state.setConversationUser(1)
  assert.equal(state.activeSessionId.value, firstSession)
  assert.equal(state.conversations.value[0].title, 'Account A private title')
  assert.equal(state.conversations.value.length, 1)
  state.setConversationUser(null)
  delete globalThis.localStorage
})

test('最近对话按消息时间倒序，恢复旧记录不按访问时间置顶，继续提问后排到最前', async t => {
  const saved = [
    { id: 'sess-old', title: '旧对话', updatedAt: 1000 },
    { id: 'sess-new', title: '新对话', updatedAt: 5000 },
    { id: 'sess-legacy', title: '缺少时间的历史记录' },
    { id: 'sess-middle', title: '中间对话', updatedAt: '3000' },
  ]
  const values = new Map([['kb_conversations:77', JSON.stringify(saved)]])
  globalThis.localStorage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }
  const state = await import('../src/state/conversations.js')
  t.after(() => { state.setConversationUser(null); delete globalThis.localStorage })
  state.setConversationUser(77)
  const orderedIds = () => state.recentConversations.value.map(item => item.id)
  assert.deepEqual(orderedIds(), ['sess-new', 'sess-middle', 'sess-old', 'sess-legacy'])
  assert.deepEqual(state.conversations.value.map(item => item.id), saved.map(item => item.id))
  state.selectConversation('sess-old')
  state.rememberConversation('旧对话的首条问题', 'sess-old', 1000)
  assert.deepEqual(orderedIds(), ['sess-new', 'sess-middle', 'sess-old', 'sess-legacy'])
  state.rememberConversation('继续提问', 'sess-old', 6000)
  assert.deepEqual(orderedIds(), ['sess-old', 'sess-new', 'sess-middle', 'sess-legacy'])
  assert.equal(state.recentConversations.value[0].title, '旧对话')
  state.setConversationUser(null)
  state.setConversationUser(77)
  assert.equal(state.recentConversations.value[0].id, 'sess-old')
})
