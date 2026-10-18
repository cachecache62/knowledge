import assert from 'node:assert/strict'
import test from 'node:test'
import { connectQueryStream } from '../src/utils/queryStream.js'

class FakeEventSource {
  static last
  listeners = new Map()
  closeCount = 0
  constructor(url) { this.url = url; FakeEventSource.last = this }
  addEventListener(name, callback) { this.listeners.set(name, callback) }
  close() { this.closeCount += 1 }
  emit(name, data) { this.listeners.get(name)?.({ data }) }
}

function setup() {
  const events = []
  const close = connectQueryStream('/stream/test', {
    onProgress: (data) => events.push(['progress', data]),
    onDelta: (data) => events.push(['delta', data]),
    onFinal: (data) => events.push(['final', data]),
    onError: (error) => events.push(['error', error.message]),
  }, FakeEventSource)
  return { source: FakeEventSource.last, events, close }
}

test('completed 进度之后继续接收 final，保留完整答案和图片，只关闭一次', () => {
  const { source, events, close } = setup()
  source.emit('delta', JSON.stringify({ delta: '部分答案' }))
  source.emit('progress', JSON.stringify({ status: 'completed' }))
  assert.equal(source.closeCount, 0)
  source.emit('final', JSON.stringify({ answer: '完整答案', image_urls: ['https://example.com/a.png'] }))
  source.emit('error', '')
  close()
  assert.equal(source.closeCount, 1)
  assert.deepEqual(events.map(([name]) => name), ['delta', 'progress', 'final'])
  assert.deepEqual(events[2][1].image_urls, ['https://example.com/a.png'])
})

test('无效 final 返回错误并关闭连接，不会让页面持续等待', () => {
  const { source, events } = setup()
  source.emit('final', '{bad json')
  assert.equal(events[0][0], 'error')
  assert.equal(source.closeCount, 1)
})

test('业务错误和连接错误均终止 SSE', () => {
  for (const data of ['', JSON.stringify({ error: '模型不可用' })]) {
    const { source, events } = setup()
    source.emit('error', data)
    assert.equal(events[0][0], 'error')
    assert.equal(source.closeCount, 1)
  }
})

test('兼容 final_answer，failed 进度也能终止任务', () => {
  const first = setup()
  first.source.emit('final_answer', JSON.stringify({ answer: '完成' }))
  assert.equal(first.events[0][0], 'final')
  assert.equal(first.source.closeCount, 1)
  const second = setup()
  second.source.emit('progress', JSON.stringify({ status: 'failed' }))
  assert.deepEqual(second.events.map(([name]) => name), ['progress', 'error'])
  assert.equal(second.source.closeCount, 1)
})
