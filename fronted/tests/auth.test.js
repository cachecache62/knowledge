import assert from 'node:assert/strict'
import test from 'node:test'
import { safeDestination } from '../src/utils/authRedirect.js'

test('登录后只跳转到允许的业务路由', () => {
  assert.equal(safeDestination('/import'), '/import')
  assert.equal(safeDestination('/documents'), '/documents')
  assert.equal(safeDestination('/chat?source=login'), '/chat?source=login')
  for (const path of ['https://evil.example', '//evil.example', '/login', '/register', '/chat/../../login', null, ['/import']]) {
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
