// 管理页的本地预览模型；不代表服务端文档或检索索引的实际状态。
export const versionLabels = { draft: '草稿', published: '已发布', archived: '已归档' }

export function createExampleDocuments() {
  const definitions = [
    [9, 'H3C LA2608 室内无线网关用户手册.pdf', 'document', ['published', 'draft']],
    [8, 'H3C ER2100 企业级路由器用户手册.pdf', 'document', ['archived', 'published']],
    [7, '产品保修与售后服务', 'faq', ['draft']],
    [6, 'CS2610DNW 打印机用户手册.pdf', 'document', ['published']],
    [5, '订单配送与物流说明', 'faq', ['archived', 'published']],
    [4, '设备安装与初次使用指南.md', 'document', ['published']],
    [3, '退换货政策与申请流程', 'faq', ['draft']],
    [2, '常见网络故障排查.md', 'document', ['archived', 'archived', 'published']],
    [1, '如何查找设备型号与序列号？', 'faq', ['published']],
  ]
  return definitions.map(([id, title, kind, statuses]) => ({
    id, title, kind, scope: '共享知识库', enabled: id !== 4,
    updatedAt: `2026-10-${String(14 + id).padStart(2, '0')}T02:30:00Z`,
    versions: statuses.map((status, index) => ({
      number: index + 1, status,
      createdAt: `2026-10-${String(10 + id + index).padStart(2, '0')}T02:30:00Z`,
      note: index ? '补充内容并更新说明' : '创建初始版本',
      ...(kind === 'faq'
        ? { question: title, answer: '这是一条示例回答。你可以新建版本，在此预览 FAQ 内容的编辑与发布流程。' }
        : { filename: title }),
    })),
  }))
}

export function latestVersion(document) {
  return document.versions.reduce((latest, version) => version.number > (latest?.number ?? 0) ? version : latest, null)
}

export function publishedVersion(document) {
  return document.versions.find(version => version.status === 'published') ?? null
}

export function documentStatus(document) {
  return document.enabled ? latestVersion(document)?.status ?? 'draft' : 'disabled'
}

export function filterDocuments(documents, { keyword = '', status = '', kind = '' } = {}) {
  const query = keyword.trim().toLowerCase()
  return documents.filter(document => {
    const currentStatus = documentStatus(document)
    return (!kind || document.kind === kind)
      && (!status || currentStatus === status)
      && (!query || `${document.title} ${document.id} ${versionLabels[currentStatus] ?? '已停用'} ${currentStatus}`.toLowerCase().includes(query))
  })
}

export function addDraft(document, content, now = new Date().toISOString()) {
  if (document.versions.some(version => version.status === 'draft')) throw new Error('已有草稿，请先编辑或发布该草稿。')
  const draft = { ...content, number: (latestVersion(document)?.number ?? 0) + 1, status: 'draft', createdAt: now }
  document.versions.push(draft)
  document.updatedAt = now
  return draft
}

export function publishVersion(document, number, now = new Date().toISOString()) {
  if (!document.enabled) throw new Error('请先启用该文档。')
  const target = document.versions.find(version => version.number === number)
  if (!target) throw new Error('未找到该版本。')
  for (const version of document.versions) {
    if (version.status === 'published') version.status = 'archived'
  }
  target.status = 'published'
  document.updatedAt = now
}

export function readDocuments(userId, storage = localStorage) {
  const saved = storage.getItem(`kb_document_preview:${userId}`)
  if (saved === null) return createExampleDocuments()
  const documents = JSON.parse(saved)
  if (!Array.isArray(documents) || !documents.every(document => document
    && Number.isSafeInteger(document.id) && typeof document.title === 'string'
    && ['document', 'faq'].includes(document.kind) && typeof document.scope === 'string'
    && typeof document.enabled === 'boolean' && typeof document.updatedAt === 'string'
    && Array.isArray(document.versions) && document.versions.length > 0
    && document.versions.every(version => version && Number.isSafeInteger(version.number)
      && Object.hasOwn(versionLabels, version.status) && typeof version.createdAt === 'string'))) {
    throw new Error('本地文档记录格式不正确。')
  }
  return documents
}

export function writeDocuments(userId, documents, storage = localStorage) {
  storage.setItem(`kb_document_preview:${userId}`, JSON.stringify(documents))
}
