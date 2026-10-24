import { readFile } from 'node:fs/promises'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, h, KeepAlive, nextTick } from 'vue'

export async function loadView(url, replacements = []) {
  const { descriptor } = parse(await readFile(url, 'utf8'))
  let source = compileScript(descriptor, {
    id: 'view-test', inlineTemplate: true,
    templateOptions: { compilerOptions: { hoistStatic: false } },
  }).content.replace(/from (['"])vue\1/g, `from '${import.meta.resolve('vue')}'`)
    .replace(/^import AppIcon from .*$/m, 'const AppIcon = { render: () => null }')
  for (const [pattern, replacement] of replacements) source = source.replace(pattern, replacement)
  return (await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))).default
}

function node(type, text = '') {
  return { type, text, children: [], props: {}, parent: null,
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
  createElement: type => node(type), createText: text => node('#text', text), createComment: text => node('#comment', text),
  setText: (item, text) => { item.text = text },
  setElementText: (item, text) => { item.text = text; item.children = [] },
  parentNode: item => item.parent, nextSibling: item => item.parent?.children[item.parent.children.indexOf(item) + 1],
  patchProp: (item, key, previous, value) => { item.props[key] = value }, remove,
  insert(child, parent, anchor = null) {
    remove(child)
    const index = anchor ? parent.children.indexOf(anchor) : -1
    parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
    child.parent = parent
  },
})

export function contents(item) {
  return (item.type === '#comment' ? '' : item.text) + item.children.map(contents).join('')
}

export function find(item, predicate) {
  if (predicate(item)) return item
  for (const child of item.children) {
    const match = find(child, predicate)
    if (match) return match
  }
}

export async function flush() {
  await new Promise(setImmediate)
  await nextTick()
}

export async function mountView(t, component) {
  const container = node('root')
  const app = renderer.createApp({ render: () => h(KeepAlive, null, { default: () => h(component) }) })
  app.component('RouterLink', { props: ['to'], setup: (props, { slots }) => () => h('a', { href: props.to }, slots.default?.()) })
  t.after(() => app.unmount())
  app.mount(container)
  await flush()
  return { container, text: () => contents(container),
    find: predicate => find(container, predicate),
    button: label => find(container, item => item.type === 'button' && (item.props['aria-label'] === label || contents(item) === label)),
  }
}
