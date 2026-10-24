import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, h, KeepAlive, nextTick, ref } from 'vue'

// 编译并运行真实页面；用 Vue 内存渲染器隔离浏览器和外部服务。
const source = await readFile(new URL('../src/views/DocumentsView.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const compiled = compileScript(descriptor, {
  id: 'documents-test', inlineTemplate: true,
  templateOptions: { compilerOptions: { hoistStatic: false } },
}).content
  .replace(/from (['"])vue\1/g, `from '${import.meta.resolve('vue')}'`)
  .replace(/^import AppIcon from .*$/m, 'const AppIcon = { render: () => null }')
  .replace(/^import \{ IMPORT_API_BASE, requestBlob, requestJson \} from .*$/m, `
    const IMPORT_API_BASE = 'http://testserver'
    const requestJson = (...args) => globalThis.documentTestRequest(...args)
    const requestBlob = () => { throw new Error('unexpected download') }
  `)
const { default: DocumentsView } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'))

function node(type, text = '') {
  return {
    type, text, children: [], props: {}, parent: null,
    get options() { return this.children.filter(child => child.type === 'option') },
    addEventListener() {}, showModal() {}, close() {}, focus() {},
  }
}

function remove(child) {
  if (!child.parent) return
  const siblings = child.parent.children
  siblings.splice(siblings.indexOf(child), 1)
  child.parent = null
}

const renderer = createRenderer({
  createElement: type => node(type),
  createText: text => node('#text', text),
  createComment: text => node('#comment', text),
  setText: (item, text) => { item.text = text },
  setElementText: (item, text) => { item.text = text; item.children = [] },
  parentNode: item => item.parent,
  nextSibling: item => item.parent?.children[item.parent.children.indexOf(item) + 1],
  patchProp: (item, key, previous, value) => { item.props[key] = value },
  insert(child, parent, anchor = null) {
    remove(child)
    const index = anchor ? parent.children.indexOf(anchor) : -1
    parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
    child.parent = parent
  },
  remove,
})

function contents(item) {
  return (item.type === '#comment' ? '' : item.text) + item.children.map(contents).join('')
}

function find(item, predicate) {
  if (predicate(item)) return item
  for (const child of item.children) {
    const match = find(child, predicate)
    if (match) return match
  }
}

async function flush() {
  await new Promise(setImmediate)
  await nextTick()
}

async function mount(t, request, initialTrash = false) {
  globalThis.documentTestRequest = request
  const trash = ref(initialTrash)
  const TrashView = { setup: () => () => h(DocumentsView, { trash: true }) }
  const container = node('root')
  const app = renderer.createApp({
    setup: () => () => h(KeepAlive, null, { default: () => trash.value ? h(TrashView) : h(DocumentsView) }),
  })
  app.component('RouterLink', { setup: (props, { slots }) => () => h('a', null, slots.default?.()) })
  app.mount(container)
  t.after(() => { app.unmount(); delete globalThis.documentTestRequest })
  await flush()
  return {
    container,
    button: label => find(container, item => item.type === 'button' && (item.props['aria-label'] === label || contents(item) === label)),
    async switchToTrash(value) { trash.value = value; await flush() },
  }
}

const document = {
  task_id: 'task-1', filename: '设备手册.pdf', scope: 'shared_kb', status: 'completed',
  created_at: '2026-10-24T07:00:00Z', updated_at: '2026-10-24T07:00:00Z',
  deleted_at: '2026-10-24T08:00:00Z', can_restore: true,
}

test('删除进入回收站，恢复防止重复提交，并刷新缓存的文档管理页', async t => {
  let deleted = false
  let restoreCalls = 0
  let finishRestore
  const screen = await mount(t, async (base, path, options = {}) => {
    if (options.method === 'DELETE') { deleted = true; return {} }
    if (options.method === 'POST') {
      restoreCalls++
      await new Promise(resolve => { finishRestore = resolve })
      deleted = false
      return {}
    }
    const visible = path.startsWith('/documents/trash?') ? deleted : !deleted
    return { total: visible ? 1 : 0, items: visible ? [document] : [] }
  })
  assert.ok(screen.button('删除 设备手册.pdf'))
  await screen.button('删除 设备手册.pdf').props.onClick()
  await screen.button('移入回收站').props.onClick()
  await flush()
  assert.equal(screen.button('删除 设备手册.pdf'), undefined)
  await screen.switchToTrash(true)
  assert.match(contents(screen.container), /删除时间（北京）/)
  const restore = screen.button('恢复 设备手册.pdf')
  const pending = restore.props.onClick()
  await restore.props.onClick()
  await flush()
  assert.equal(restoreCalls, 1)
  assert.equal(restore.props.disabled, true)
  finishRestore()
  await pending
  await flush()
  assert.match(contents(screen.container), /回收站为空/)
  await screen.switchToTrash(false)
  assert.ok(screen.button('删除 设备手册.pdf'))
})

test('恢复冲突保留当前文档并展示原因，历史清理记录禁止恢复', async t => {
  let restoreCalls = 0
  const old = { ...document, task_id: 'old', filename: '旧文档.pdf', can_restore: false, deleted_at: null }
  const screen = await mount(t, async (base, path, options = {}) => {
    if (options.method === 'POST') {
      restoreCalls++
      throw Object.assign(new Error('知识库中已存在相同内容的文档'), { status: 409 })
    }
    return { total: 2, items: [document, old] }
  }, true)
  await screen.button('恢复 设备手册.pdf').props.onClick()
  await flush()
  assert.match(contents(screen.container), /恢复失败：知识库中已存在相同内容的文档/)
  assert.ok(screen.button('恢复 设备手册.pdf'))
  const disabled = screen.button('恢复 旧文档.pdf')
  assert.equal(disabled.props.disabled, true)
  assert.match(disabled.props.title, /旧版本清理/)
  await disabled.props.onClick()
  assert.equal(restoreCalls, 1)
})

test('恢复最后一页唯一文档后自动返回有效页码', async t => {
  let restored = false
  const pages = []
  const firstPage = Array.from({ length: 10 }, (_, index) => ({ ...document, task_id: `first-${index}` }))
  const screen = await mount(t, async (base, path, options = {}) => {
    if (options.method === 'POST') { restored = true; return {} }
    const page = Number(new URL(path, base).searchParams.get('page'))
    pages.push(page)
    return { total: restored ? 10 : 11, items: page === 1 ? firstPage : restored ? [] : [document] }
  }, true)
  screen.button('下一页').props.onClick()
  await flush()
  await screen.button('恢复 设备手册.pdf').props.onClick()
  await flush()
  assert.deepEqual(pages, [1, 2, 2, 1])
  assert.match(contents(screen.container), /1 \/ 1/)
})
