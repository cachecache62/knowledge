import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

// 只替换 Vite 注入的环境对象，执行真实请求封装，不依赖浏览器或后端。
const source = (await readFile(new URL('../src/api.js', import.meta.url), 'utf8')).replaceAll('import.meta.env', '({})')
const { requestBlob } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))

test('下载携带登录 Cookie，原样返回文件内容并传递取消信号', async t => {
  const controller = new AbortController()
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  globalThis.fetch = async (url, options) => {
    assert.equal(url, 'http://localhost:8000/documents/task/download')
    assert.equal(options.credentials, 'include')
    assert.equal(options.signal, controller.signal)
    return new Response(new Uint8Array([0, 1, 2, 255]), { headers: { 'Content-Type': 'application/octet-stream' } })
  }
  const blob = await requestBlob('http://localhost:8000', '/documents/task/download', { signal: controller.signal })
  assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), new Uint8Array([0, 1, 2, 255]))
})

test('文件缺失展示后端原因；登录失效触发已有鉴权处理，不把错误 JSON 当文件下载', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  t.after(() => { globalThis.fetch = originalFetch; globalThis.window = originalWindow })
  const events = []
  globalThis.window = { dispatchEvent: event => events.push(event.type) }
  globalThis.fetch = async () => Response.json({ detail: '原文档文件不存在' }, { status: 404 })
  await assert.rejects(requestBlob('', '/documents/task/download'), error => error.status === 404 && error.message === '原文档文件不存在')
  assert.deepEqual(events, [])
  globalThis.fetch = async () => Response.json({ detail: '请先登录' }, { status: 401 })
  await assert.rejects(requestBlob('', '/documents/task/download'), error => error.status === 401)
  assert.deepEqual(events, ['kb:unauthorized'])
})
