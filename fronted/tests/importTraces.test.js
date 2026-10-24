import assert from 'node:assert/strict'
import test from 'node:test'
import { reactive } from 'vue'
import { contents, flush, loadView, mountView } from './helpers/vueHarness.js'

const View = await loadView(new URL('../src/views/ImportTracesView.vue', import.meta.url), [
  [/^import \{ useRoute \} from .*$/m, 'const useRoute = () => globalThis.traceTestRoute'],
  [/^import \{ IMPORT_API_BASE, requestBlob, requestJson \} from .*$/m, `
    const IMPORT_API_BASE = 'http://testserver'
    const requestJson = (...args) => globalThis.traceTestRequest(...args)
    const requestBlob = (...args) => globalThis.traceTestBlob(...args)
  `],
])

const taskA = '7633e484-3bfa-4bdc-a055-05b7c8bb3e99'
const taskB = '11111111-1111-4111-8111-111111111111'
function record(taskId = taskA) {
  return { task_id: taskId, trace_id: 'trace-' + taskId, filename: '设备手册.pdf', route: 'pdf', scope: 'shared_kb',
    user_id: 1, status: 'failed', started_at: '2026-10-24T07:00:00Z', finished_at: '2026-10-24T07:00:02Z',
    duration_ms: 2000, warning_count: 0, error: '切片失败', snapshot_policy: '长文本和向量为摘要快照。',
    nodes: [
      { key: 'node_entry', sequence: 1, label: '检查文件', provider: '本地代码', status: 'success', started_at: '2026-10-24T07:00:00Z', duration_ms: 10, upstream: null, input: { file: '设备手册.pdf' }, output: { selected_route: 'pdf' }, warnings: [], error: '' },
      { key: 'node_document_split', sequence: 2, label: '文档切分', provider: '本地代码', status: 'failed', started_at: '2026-10-24T07:00:01Z', duration_ms: 20, upstream: 'node_entry', input: { text: '资料' }, output: null, error: '切片失败', warnings: [] },
    ],
  }
}

async function setup(t, params, request) {
  globalThis.traceTestRoute = reactive({ params })
  globalThis.traceTestRequest = request
  globalThis.traceTestBlob = async () => new Blob(['{}'], { type: 'application/json' })
  t.after(() => { delete globalThis.traceTestRoute; delete globalThis.traceTestRequest; delete globalThis.traceTestBlob })
  return mountView(t, View)
}

test('导入列表读取真实接口，搜索后重置页码，并链接到任务详情', async t => {
  const paths = []
  const screen = await setup(t, {}, async (base, path) => {
    paths.push(path)
    const keyword = new URL(path, base).searchParams.get('keyword')
    return { total: keyword ? 0 : 1, items: keyword ? [] : [record()] }
  })
  assert.match(screen.text(), /设备手册.pdf/)
  assert.equal(screen.find(item => item.type === 'a' && contents(item) === '查看详情').props.href, '/import-traces/' + taskA)
  const input = screen.find(item => item.type === 'input')
  input.value = '不存在'
  input.props['onUpdate:modelValue']('不存在')
  screen.find(item => item.type === 'form').props.onSubmit({ preventDefault() {} })
  await flush()
  assert.match(paths.at(-1), /page=1/)
  assert.match(screen.text(), /请调整筛选条件/)
})

test('详情默认定位失败节点，可查看输入输出、切换流程图和参数来源', async t => {
  const screen = await setup(t, { taskId: taskA }, async () => record())
  assert.match(screen.text(), /切片失败/)
  assert.ok(screen.button('错误与提示').props['aria-selected'])
  screen.find(item => item.type === 'button' && item.props['aria-pressed'] === false).props.onClick()
  await flush()
  assert.match(screen.text(), /设备手册.pdf/)
  screen.button('输出结果').props.onClick()
  await flush()
  assert.match(screen.text(), /selected_route/)
  screen.button('流程图').props.onClick()
  await flush()
  assert.ok(screen.find(item => item.props['aria-label'] === '文档导入流程图'))
  screen.button('文档与参数来源').props.onClick()
  await flush()
  assert.match(screen.text(), /扩展名为 .pdf/)
  screen.button('导入结果').props.onClick()
  await flush()
  assert.match(screen.text(), /切片失败/)
})

test('运行中的详情自动刷新，完成后停止轮询', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let calls = 0
  const screen = await setup(t, { taskId: taskA }, async () => {
    calls++
    return { ...record(), status: calls === 1 ? 'processing' : 'completed', error: '' }
  })
  assert.match(screen.text(), /每 3 秒自动刷新/)
  t.mock.timers.tick(3000)
  await flush()
  assert.equal(calls, 2)
  assert.doesNotMatch(screen.text(), /每 3 秒自动刷新/)
  t.mock.timers.tick(6000)
  await flush()
  assert.equal(calls, 2)
})

test('切换任务后忽略旧请求的迟到响应，旧任务缺记录时显示原因', async t => {
  let resolveFirst
  const screen = await setup(t, { taskId: taskA }, async (base, path) => {
    if (path.endsWith(taskA)) return new Promise(resolve => { resolveFirst = resolve })
    return { ...record(taskB), filename: '新的文档.md' }
  })
  globalThis.traceTestRoute.params.taskId = taskB
  await flush()
  resolveFirst(record(taskA))
  await flush()
  assert.match(screen.text(), /新的文档.md/)
  assert.doesNotMatch(screen.text(), /trace-7633e484/)
  globalThis.traceTestRequest = async () => { throw Object.assign(new Error('missing'), { status: 404 }) }
  globalThis.traceTestRoute.params.taskId = taskA
  await flush()
  assert.match(screen.text(), /历史任务可能尚无节点记录/)
})

test('导出调用受保护的 JSON 接口，并使用当前任务文件名', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let downloaded = false
  let downloadName = ''
  let path
  const priorDocument = globalThis.document
  globalThis.document = {
    createElement: () => ({ set download(value) { downloadName = value }, click() { downloaded = true }, remove() {} }),
    body: { appendChild() {} },
  }
  t.after(() => { if (priorDocument === undefined) delete globalThis.document; else globalThis.document = priorDocument })
  t.mock.method(URL, 'createObjectURL', () => 'blob:trace')
  t.mock.method(URL, 'revokeObjectURL', () => {})
  const screen = await setup(t, { taskId: taskA }, async () => record())
  globalThis.traceTestBlob = async (base, value) => { path = value; return new Blob(['{}']) }
  await screen.button('导出 JSON').props.onClick()
  assert.equal(path, `/import-traces/${taskA}/export`)
  assert.equal(downloadName, `import-trace-${taskA}.json`)
  assert.ok(downloaded)
  t.mock.timers.tick(1000)
})
