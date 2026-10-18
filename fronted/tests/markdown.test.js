import test from 'node:test'
import assert from 'node:assert/strict'
import { inlineTokens, markdownBlocks } from '../src/utils/markdown.js'

test('代码块保留缩进和 Markdown 符号，不将其解析成标题或列表', () => {
  const blocks = markdownBlocks('# 操作步骤\r\n\r\n```python\r\n  # 注释\r\n  print("<script>")\r\n```\r\n\r\n3. 执行\r\n4. 检查')
  assert.deepEqual(blocks, [
    { type: 'heading', level: 1, text: '操作步骤' },
    { type: 'code', language: 'python', text: '  # 注释\n  print("<script>")' },
    { type: 'list', ordered: true, start: 3, items: ['执行', '检查'] },
  ])
})

test('流式返回未闭合的代码围栏时仍展示已收到的代码', () => {
  assert.deepEqual(markdownBlocks('先执行：\n\n```powershell\nuv run python -m api.import_service'), [
    { type: 'paragraph', text: '先执行：' },
    { type: 'code', language: 'powershell', text: 'uv run python -m api.import_service' },
  ])
})

test('模型返回的 HTML 和危险链接保留为文字，只识别 HTTP 链接', () => {
  const tokens = inlineTokens('<img src=x onerror=alert(1)> [危险](javascript:alert(1)) **重点** `uv` [文档](https://example.com/docs)')
  assert.equal(tokens[0].type, 'text')
  assert.ok(tokens[0].text.includes('<img src=x onerror=alert(1)>'))
  assert.ok(tokens[0].text.includes('[危险](javascript:alert(1))'))
  assert.equal(tokens.filter(item => item.type === 'link').length, 1)
  assert.equal(tokens.at(-1).href, 'https://example.com/docs')
})
