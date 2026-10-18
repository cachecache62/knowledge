import assert from 'node:assert/strict'
import test from 'node:test'
import { parseAnswer } from '../src/utils/answer.js'

test('合并图片区块、正文和后端图片，保序去重并保留查询参数', () => {
  const result = parseAnswer('请参考示意图。\n【图片】\nhttps://example.com/panel.png?v=1', [
    'https://example.com/panel.png?v=1', 'https://example.com/knob.jpg',
  ])
  assert.equal(result.text, '请参考示意图。')
  assert.deepEqual(result.images, ['https://example.com/panel.png?v=1', 'https://example.com/knob.jpg'])
})

test('支持 Markdown 图片、中文标点和 URL 空格', () => {
  const result = parseAnswer('![面板](https://example.com/my panel.png)\n另见 https://example.com/knob.jpg。')
  assert.deepEqual(result.images, ['https://example.com/my%20panel.png', 'https://example.com/knob.jpg'])
})

test('不把危险协议或普通页面当作图片，不丢失无图片区块之后的文字', () => {
  const result = parseAnswer('说明【图片】暂不可用', ['javascript:alert(1)', 'data:image/png;base64,abc', 'https://example.com/page'])
  assert.equal(result.text, '说明【图片】暂不可用')
  assert.deepEqual(result.images, [])
})
