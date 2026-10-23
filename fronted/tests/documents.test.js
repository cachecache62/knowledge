import assert from 'node:assert/strict'
import test from 'node:test'
import { addDraft, createExampleDocuments, documentStatus, filterDocuments, latestVersion, publishedVersion, publishVersion, readDocuments, writeDocuments } from '../src/utils/documents.js'

test('发布与回滚只保留一个发布版本，并保留原始内容和待发布草稿', () => {
  const document = createExampleDocuments().find(item => item.id === 8)
  const original = structuredClone(document.versions)
  const draft = addDraft(document, { filename: 'manual-v3.pdf', note: '第三版' }, '2026-10-23T12:00:00Z')
  assert.equal(draft.number, 3)
  assert.equal(publishedVersion(document).number, 2)
  assert.throws(() => addDraft(document, {}), /已有草稿/)
  publishVersion(document, 1)
  assert.equal(publishedVersion(document).number, 1)
  assert.equal(document.versions.filter(version => version.status === 'published').length, 1)
  assert.equal(document.versions.find(version => version.number === 2).status, 'archived')
  assert.equal(latestVersion(document).status, 'draft')
  assert.equal(document.versions[0].filename, original[0].filename)
  publishVersion(document, 3)
  assert.equal(publishedVersion(document).number, 3)
  assert.equal(document.versions[0].status, 'archived')
})

test('停用文档不能发布，无效版本不会改变现有发布状态', () => {
  const document = createExampleDocuments().find(item => item.id === 4)
  assert.equal(documentStatus(document), 'disabled')
  const original = structuredClone(document.versions)
  assert.throws(() => publishVersion(document, 1), /先启用/)
  document.enabled = true
  assert.throws(() => publishVersion(document, 99), /未找到/)
  assert.deepEqual(document.versions, original)
})

test('组合筛选按最新版本状态和类型匹配，停用状态单独筛选', () => {
  const documents = createExampleDocuments()
  assert.deepEqual(filterDocuments(documents, { keyword: 'H3C', kind: 'document', status: 'draft' }).map(item => item.id), [9])
  assert.deepEqual(filterDocuments(documents, { keyword: '  DISABLED  ' }).map(item => item.id), [4])
  assert.equal(filterDocuments(documents, { keyword: '不存在' }).length, 0)
  assert.equal(filterDocuments(documents, { kind: 'faq', status: 'draft' }).length, 2)
  assert.equal(filterDocuments(documents, { status: 'published' }).some(item => !item.enabled), false)
})

test('本地预览按账号隔离，清空列表后不自动复活示例记录，损坏记录明确报错', () => {
  const values = new Map()
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }
  const first = readDocuments(1, storage)
  first[0].title = '账号一的文档'
  writeDocuments(1, first, storage)
  assert.equal(readDocuments(1, storage)[0].title, '账号一的文档')
  assert.notEqual(readDocuments(2, storage)[0].title, '账号一的文档')
  writeDocuments(1, [], storage)
  assert.deepEqual(readDocuments(1, storage), [])
  storage.setItem('kb_document_preview:1', '[{"id": 1}]')
  assert.throws(() => readDocuments(1, storage), /格式不正确/)
})
