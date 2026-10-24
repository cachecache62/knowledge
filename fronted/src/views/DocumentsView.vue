<script setup>
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, reactive, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { IMPORT_API_BASE, requestBlob, requestJson } from '../api.js'

const props = defineProps({ trash: { type: Boolean, default: false } })
const documents = ref([])
const total = ref(0)
const loading = ref(false)
const loadError = ref('')
const filters = reactive({ keyword: '', status: '' })
const appliedFilters = reactive({ keyword: '', status: '' })
const page = ref(1)
const pageSize = ref(10)
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const hasFilters = computed(() => Boolean(appliedFilters.keyword || appliedFilters.status))
const statusLabels = { pending: '待处理', processing: '处理中', completed: '已完成', failed: '失败' }
const downloading = reactive(new Set())
const restoring = reactive(new Set())
const deleteTarget = ref(null)
const deleting = ref(false)
const deleteError = ref('')
const deleteDialog = ref(null)
const cancelDeleteButton = ref(null)
const notice = ref('')
const noticeError = ref(false)
let noticeTimer
let activeRequest
let active = true
const downloadRequests = new Map()
const downloadUrls = new Set()

function notify(message, error = false) {
  clearTimeout(noticeTimer)
  notice.value = message
  noticeError.value = error
  noticeTimer = setTimeout(() => { notice.value = '' }, 6000)
}

async function loadList(showNotice = false) {
  activeRequest?.abort()
  const controller = new AbortController()
  activeRequest = controller
  loading.value = true
  loadError.value = ''
  const params = new URLSearchParams({
    page: String(page.value), page_size: String(pageSize.value), keyword: appliedFilters.keyword,
  })
  if (appliedFilters.status) params.set('status', appliedFilters.status)
  try {
    const data = await requestJson(IMPORT_API_BASE, (props.trash ? '/documents/trash?' : '/documents?') + params, {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
    if (activeRequest !== controller || controller.signal.aborted) return
    if (!Array.isArray(data.items) || !Number.isInteger(data.total)) throw new Error('服务端返回的文档列表格式不正确')
    total.value = data.total
    if (page.value > pageCount.value) {
      page.value = pageCount.value
      await loadList(showNotice)
      return
    }
    documents.value = data.items
    if (showNotice) notify(props.trash ? '回收站已刷新。' : '文档列表已刷新。')
  } catch (error) {
    if (activeRequest === controller && !controller.signal.aborted) {
      loadError.value = '加载文档失败：' + error.message
    }
  } finally {
    if (activeRequest === controller) loading.value = false
  }
}

function applyFilters() {
  Object.assign(appliedFilters, { keyword: filters.keyword.trim(), status: filters.status })
  page.value = 1
  loadList()
}

function resetFilters() {
  Object.assign(filters, { keyword: '', status: '' })
  applyFilters()
}

function changePage(value) {
  if (value < 1 || value > pageCount.value || value === page.value) return
  page.value = value
  loadList()
}

function changePageSize() {
  page.value = 1
  loadList()
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  })
}

function isImporting(item) {
  return ['pending', 'processing'].includes(item.status)
}

// 下载文档文件第一版 直接使用 requestBlob 下载，避免浏览器对大文件的内存占用过高
async function downloadDocument1(item) {
  if (downloading.has(item.task_id) || deleting.value && deleteTarget.value?.task_id === item.task_id) return
  const controller = new AbortController()
  downloadRequests.set(item.task_id, controller)
  downloading.add(item.task_id)
  try {
    const blob = await requestBlob(IMPORT_API_BASE, '/documents/' + encodeURIComponent(item.task_id) + '/download', {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(60000)]),
    })
    if (controller.signal.aborted) return
    const url = URL.createObjectURL(blob)
    downloadUrls.add(url)
    const link = document.createElement('a')
    link.href = url
    link.download = item.filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => { URL.revokeObjectURL(url); downloadUrls.delete(url) }, 1000)
    notify('文件已准备好，请在浏览器中确认保存：' + item.filename)
  } catch (error) {
    if (!controller.signal.aborted) notify('下载失败：' + error.message, true)
  } finally {
    downloading.delete(item.task_id)
    downloadRequests.delete(item.task_id)
  }
}

async function downloadDocument(item) {
  if (
    downloadRequests.has(item.task_id) ||
    downloading.has(item.task_id) ||
    (deleting.value && deleteTarget.value?.task_id === item.task_id)
  ) return

  if (typeof window.showSaveFilePicker !== 'function') {
    notify('当前浏览器不支持先选择保存位置，请使用支持此功能的 Chrome 或 Edge。', true)
    return
  }

  const controller = new AbortController()
  downloadRequests.set(item.task_id, controller)

  let writable

  try {
    // ① 先选择保存位置，此时还没有向后端请求文件。
    const fileHandle = await window.showSaveFilePicker({
      suggestedName: item.filename,
    })

    // 用户点击“保存”后，才执行到这里。
    if (controller.signal.aborted) return

    downloading.add(item.task_id)

    // ② 向后端请求文件。
    const blob = await requestBlob(
      IMPORT_API_BASE,
      '/documents/' + encodeURIComponent(item.task_id) + '/download',
      {
        signal: AbortSignal.any([
          controller.signal,
          AbortSignal.timeout(60000),
        ]),
      }
    )

    if (controller.signal.aborted) return

    // ③ 将文件写入用户选择的位置。
    writable = await fileHandle.createWritable()
    await writable.write(blob)
    await writable.close()
    writable = null

    notify('文件已保存：' + item.filename)
  } catch (error) {
    // 写入失败时放弃尚未提交的写入。
    if (writable) {
      try {
        await writable.abort()
      } catch {
        // 保留原始错误。
      }
    }

    // 用户取消保存窗口，正常结束，不提示下载失败。
    if (error.name === 'AbortError' || controller.signal.aborted) return

    notify('下载或保存失败：' + error.message, true)
  } finally {
    downloading.delete(item.task_id)
    downloadRequests.delete(item.task_id)
  }
}

async function askDelete(item) {
  if (isImporting(item) || deleting.value) return
  deleteTarget.value = item
  deleteError.value = ''
  await nextTick()
  deleteDialog.value.showModal()
  cancelDeleteButton.value?.focus()
}

function closeDelete() {
  if (deleting.value) return
  deleteDialog.value?.close()
  deleteTarget.value = null
  deleteError.value = ''
}

async function confirmDelete() {
  if (!deleteTarget.value || deleting.value) return
  deleting.value = true
  deleteError.value = ''
  try {
    await requestJson(IMPORT_API_BASE, '/documents/' + encodeURIComponent(deleteTarget.value.task_id), {
      method: 'DELETE', signal: AbortSignal.timeout(120000),
    })
    deleting.value = false
    closeDelete()
    notify('文档已移入回收站，可随时恢复。')
    if (active) await loadList()
  } catch (error) {
    if (error.status === 404) {
      deleting.value = false
      closeDelete()
      notify('文档已不存在，列表已更新。')
      if (active) await loadList()
    } else {
      deleteError.value = error.name === 'TimeoutError'
        ? '删除结果尚未确认，请稍后刷新列表查看。'
        : '删除失败：' + error.message
    }
  } finally {
    deleting.value = false
  }
}

async function restoreDocument(item) {
  if (!item.can_restore || restoring.has(item.task_id)) return
  restoring.add(item.task_id)
  try {
    await requestJson(IMPORT_API_BASE, '/documents/' + encodeURIComponent(item.task_id) + '/restore', {
      method: 'POST', signal: AbortSignal.timeout(30000),
    })
    notify('文档已恢复，可在文档管理中查看。')
    if (active) await loadList()
  } catch (error) {
    if (error.status === 404) {
      notify('文档已不存在，列表已更新。', true)
      if (active) await loadList()
    } else {
      notify(error.name === 'TimeoutError'
        ? '恢复结果尚未确认，请刷新列表查看。'
        : '恢复失败：' + error.message, true)
    }
  } finally {
    restoring.delete(item.task_id)
  }
}

function cancelReads() {
  activeRequest?.abort()
  for (const controller of downloadRequests.values()) controller.abort()
}

onActivated(() => { active = true; loadList() })
onDeactivated(() => {
  active = false
  cancelReads()
  if (!deleting.value) closeDelete()
  else deleteDialog.value?.close()
})
onBeforeUnmount(() => {
  active = false
  cancelReads()
  clearTimeout(noticeTimer)
  for (const url of downloadUrls) URL.revokeObjectURL(url)
  deleteDialog.value?.close()
})
</script>

<template>
  <main class="documents-page">
    <div class="documents-topline">
      <span>{{ trash ? '找回误删文档，让知识继续可用。' : '管理知识内容，让每一次回答都有据可依。' }}</span>
      <button class="document-button" :disabled="loading" @click="loadList(true)">
        <AppIcon name="refresh" :size="15" />{{ loading ? '刷新中…' : '刷新列表' }}
      </button>
    </div>

    <section class="documents-panel" aria-labelledby="documents-title">
      <header class="documents-heading">
        <div>
          <div class="documents-title-line"><h1 id="documents-title">{{ trash ? '回收站' : '知识库文档管理' }}</h1></div>
          <p>{{ trash ? '已删除的文档会保留在这里。恢复后可重新查看，已完成导入的文档将继续参与知识检索。' : '查看文档导入状态、下载原始文件，或将不再需要的文档移入回收站。' }}</p>
        </div>
        <div class="documents-heading-actions">
          <RouterLink v-if="trash" to="/documents" class="document-button"><AppIcon name="file" :size="16" />返回文档管理</RouterLink>
          <template v-else>
            <RouterLink to="/trash" class="document-button"><AppIcon name="trash" :size="16" />回收站</RouterLink>
            <RouterLink to="/import" class="document-button primary"><AppIcon name="upload" :size="16" />上传文档</RouterLink>
          </template>
        </div>
      </header>

      <div class="documents-body">
        <form class="documents-filters" @submit.prevent="applyFilters">
          <label class="document-search"><AppIcon name="search" :size="17" /><input v-model="filters.keyword" aria-label="搜索文档标题或任务ID" placeholder="搜索文档标题 / 任务 ID" /></label>
          <select v-model="filters.status" aria-label="文档状态">
            <option value="">全部状态</option><option value="pending">待处理</option><option value="processing">处理中</option><option value="completed">已完成</option><option value="failed">失败</option>
          </select>
          <button type="submit" class="document-button primary" :disabled="loading">查询</button>
          <button type="button" class="document-button" @click="resetFilters">重置</button>
        </form>

        <div class="documents-list-heading"><span>{{ hasFilters ? '筛选结果' : trash ? '已删除文档' : '全部文档' }} <strong>{{ total }}</strong></span><span>{{ trash ? '仅展示当前账号删除的自有文档' : '仅展示当前账号上传的文档' }}</span></div>
        <div v-if="loadError" class="documents-load-error" role="alert"><AppIcon name="info" :size="18" /><span>{{ loadError }}</span><button class="document-link" @click="loadList()">重新加载</button></div>
        <div class="documents-table-scroll" role="region" :aria-label="trash ? '回收站文档列表' : '知识库文档列表'" :aria-busy="loading" tabindex="0">
          <table class="documents-table">
            <thead><tr><th scope="col">任务 ID</th><th scope="col" class="document-title-cell">文档标题</th><th scope="col">适用范围</th><th scope="col">{{ trash ? '原导入状态' : '导入状态' }}</th><th scope="col">创建时间（北京）</th><th scope="col">{{ trash ? '删除时间（北京）' : '更新时间（北京）' }}</th><th scope="col" class="document-actions-cell">操作</th></tr></thead>
            <tbody>
              <tr v-for="item in documents" :key="item.task_id">
                <td class="document-id-cell" :title="item.task_id">{{ item.task_id.slice(0, 8) }}</td>
                <td><div class="document-name" :title="item.filename"><span class="document-file-icon"><AppIcon name="file" :size="18" /></span><span><strong>{{ item.filename }}</strong><small>文档 · {{ (item.filename.split('.').pop() || '').toUpperCase() }}</small></span></div></td>
                <td><span class="document-scope">{{ item.scope === 'shared_kb' ? '共享知识库' : item.scope }}</span></td>
                <td><span class="document-status" :class="item.status"><i></i>{{ statusLabels[item.status] || item.status }}</span></td>
                <td class="document-date">{{ formatDate(item.created_at) }}</td>
                <td class="document-date">{{ formatDate(trash ? item.deleted_at : item.updated_at) }}</td>
                <td><div class="document-row-actions">
                  <template v-if="trash">
                    <button class="document-link document-action-button" :disabled="!item.can_restore || restoring.has(item.task_id)" :title="item.can_restore ? '恢复到文档管理' : '此文档已由旧版本清理，无法恢复，请重新上传'" :aria-label="'恢复 ' + item.filename" @click="restoreDocument(item)"><AppIcon name="history" :size="14" />{{ restoring.has(item.task_id) ? '恢复中…' : item.can_restore ? '恢复' : '无法恢复' }}</button>
                  </template>
                  <template v-else>
                  <RouterLink class="document-link document-action-button" :to="'/import-traces/' + item.task_id"><AppIcon name="history" :size="14" />导入记录</RouterLink>
                  <button class="document-link document-action-button" :disabled="downloading.has(item.task_id) || deleting && deleteTarget?.task_id === item.task_id" :aria-label="'下载 ' + item.filename" @click="downloadDocument(item)"><AppIcon name="download" :size="14" />{{ downloading.has(item.task_id) ? '下载中…' : '下载' }}</button>
                  <button class="document-link danger document-action-button" :disabled="isImporting(item) || deleting || downloading.has(item.task_id)" :title="isImporting(item) ? '文档导入完成或失败后才能删除' : '移入回收站，可恢复'" :aria-label="'删除 ' + item.filename" @click="askDelete(item)"><AppIcon name="trash" :size="14" />删除</button>
                  </template>
                </div></td>
              </tr>
              <tr v-if="!documents.length"><td colspan="7"><div class="documents-empty"><AppIcon :name="trash ? 'trash' : 'file'" :size="30" /><strong>{{ loading ? '正在加载文档…' : loadError ? '文档加载失败' : hasFilters ? '没有找到符合条件的文档' : trash ? '回收站为空' : '暂无文档' }}</strong><p v-if="!loading && !loadError">{{ hasFilters ? '试试其他关键词，或清空筛选条件。' : trash ? '移入回收站的文档会显示在这里，你可以随时恢复。' : '上传一份 PDF 或 Markdown 文档，开始建立知识库。' }}</p><button v-if="hasFilters && !loading" class="document-button" @click="resetFilters">清空筛选</button></div></td></tr>
            </tbody>
          </table>
        </div>
        <footer class="documents-pagination">
          <span>共 {{ total }} 条{{ total ? '，显示 ' + ((page - 1) * pageSize + 1) + '–' + Math.min(page * pageSize, total) + ' 条' : '' }}</span>
          <div><select v-model.number="pageSize" aria-label="每页条数" @change="changePageSize"><option :value="5">5 条 / 页</option><option :value="10">10 条 / 页</option><option :value="20">20 条 / 页</option></select><button class="document-page-button" :disabled="page <= 1 || loading" aria-label="上一页" @click="changePage(page - 1)"><AppIcon name="chevron" :size="15" class="previous" /></button><span class="document-page-current" aria-live="polite">{{ page }} / {{ pageCount }}</span><button class="document-page-button" :disabled="page >= pageCount || loading" aria-label="下一页" @click="changePage(page + 1)"><AppIcon name="chevron" :size="15" class="next" /></button></div>
        </footer>
      </div>
    </section>
    <p class="documents-footer-note">{{ trash ? '回收站中的文档不参与知识检索。' : '原文可下载，资料可管理。' }}<span>{{ trash ? '旧版本已清理的文档无法恢复，请重新上传。' : '导入中的文档暂不能删除，删除后可在回收站恢复。' }}</span></p>
    <div v-if="notice" class="document-toast" :class="{ error: noticeError }" :role="noticeError ? 'alert' : 'status'"><AppIcon :name="noticeError ? 'info' : 'check'" :size="17" /><span>{{ notice }}</span><button class="icon-button" aria-label="关闭提示" @click="notice = ''"><AppIcon name="close" :size="15" /></button></div>

    <dialog ref="deleteDialog" class="document-dialog" aria-labelledby="delete-document-title" aria-describedby="delete-document-description" @cancel.prevent="closeDelete">
      <template v-if="deleteTarget">
        <header><div><span class="document-dialog-eyebrow">资料库 · 文档管理</span><h2 id="delete-document-title">移入回收站</h2></div><button class="icon-button" :disabled="deleting" aria-label="关闭弹窗" @click="closeDelete"><AppIcon name="close" /></button></header>
        <div class="document-confirm-content">
          <div class="document-confirm-icon danger"><AppIcon name="trash" :size="24" /></div>
          <p>确定将「{{ deleteTarget.filename }}」移入回收站吗？</p>
          <p id="delete-document-description" class="document-dialog-description">移入后，文档将不再参与知识检索。原文件和知识数据会保留，你可以在回收站恢复；历史对话中的已有回答不会改变。</p>
          <p v-if="deleteError" class="message-error" role="alert">{{ deleteError }}</p>
          <footer><button ref="cancelDeleteButton" class="document-button" :disabled="deleting" @click="closeDelete">取消</button><button class="document-button danger-solid" :disabled="deleting" @click="confirmDelete">{{ deleting ? '正在移入…' : '移入回收站' }}</button></footer>
        </div>
      </template>
    </dialog>
  </main>
</template>

<style scoped>
.documents-page { --document-blue: #356fd1; --document-border: #e8ecf2; --document-muted: #667085; flex: 1; min-height: 0; overflow-y: auto; padding: 8px 28px 28px; background: #f6f8fb; color: #303b4d; font-size: 13px; }
.documents-topline { min-height: 58px; display: flex; align-items: center; justify-content: space-between; gap: 16px; color: var(--document-muted); font-size: 12px; }
.document-button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 36px; padding: 8px 14px; border: 1px solid #dce2eb; border-radius: 6px; background: #fff; color: #526075; text-decoration: none; font-size: 12px; white-space: nowrap; transition: background .15s, border-color .15s; }
.document-button:hover { background: #f2f5fa; border-color: #b6c5dc; }
.document-button.primary { background: var(--document-blue); border-color: var(--document-blue); color: #fff; }
.document-button.primary:hover { background: #285cb5; }
.document-button.danger-solid { background: #c44444; border-color: #c44444; color: white; }
.document-button.danger-solid:hover { background: #a93636; }
.documents-panel { background: #fff; border: 1px solid var(--document-border); border-radius: 10px; box-shadow: 0 2px 5px #243b5310; }
.documents-heading { display: flex; align-items: center; justify-content: space-between; gap: 22px; padding: 25px 24px 23px; border-bottom: 1px solid var(--document-border); }
.documents-title-line { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
h1 { margin: 0; color: #263346; font-size: 21px; font-weight: 650; letter-spacing: -.4px; }
.documents-preview-label { padding: 3px 7px; border: 1px solid #dee6f2; border-radius: 4px; color: #687b98; font-size: 10px; font-weight: 400; background: #f6f8fc; }
.documents-heading p { max-width: 670px; margin: 10px 0 0; color: var(--document-muted); font-size: 12px; line-height: 1.8; }
.documents-heading-actions { display: flex; flex-shrink: 0; gap: 10px; }
.documents-body { padding: 20px 24px 0; }
.documents-preview-note { display: flex; gap: 8px; align-items: flex-start; color: #667d9c; font-size: 11px; line-height: 1.7; margin-bottom: 17px; }
.documents-preview-note svg { margin-top: 1px; }
.documents-filters { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 17px; background: #f6f8fb; border-radius: 6px; }
.document-search { display: flex; gap: 8px; align-items: center; flex: 1; min-width: 200px; max-width: 340px; background: #fff; border: 1px solid #dce2eb; border-radius: 5px; padding: 0 11px; color: #8b96a6; }
.document-search:focus-within { border-color: var(--document-blue); }
.document-search input { width: 100%; min-width: 0; height: 36px; border: 0; background: transparent; font-size: 12px; color: #303b4d; outline: none; }
.document-search input::placeholder { color: #929cac; }
select { height: 38px; padding: 0 28px 0 10px; border: 1px solid #dce2eb; border-radius: 5px; background: #fff; color: #626f82; font: inherit; font-size: 12px; }
.documents-list-heading { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 23px 0 14px; color: #667085; font-size: 11px; }
.documents-list-heading > span:first-child { font-size: 13px; color: #4d5a6d; }
.documents-list-heading strong { display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 9px; background: #eef2f7; color: #6b7a90; font-size: 10px; font-weight: 500; }
.documents-table-scroll { overflow-x: auto; border: 1px solid var(--document-border); border-radius: 5px; scrollbar-width: thin; }
.documents-table { width: 100%; min-width: 1060px; border-collapse: collapse; text-align: left; table-layout: auto; }
.documents-table th { background: #f6f8fb; color: #667085; font-size: 12px; font-weight: 500; white-space: nowrap; padding: 15px 12px; border-bottom: 1px solid var(--document-border); }
.documents-table td { padding: 17px 12px; border-bottom: 1px solid #edf0f5; font-size: 12px; }
.documents-table > tbody > tr:last-child > td { border-bottom: 0; }
.documents-table > tbody > tr:nth-child(4n + 3):not(.document-expanded-row) { background: #fcfdff; }
.documents-table > tbody > tr:not(.document-expanded-row):hover { background: #f6f9ff; }
.documents-table .document-expand-cell { width: 32px; padding-left: 9px; padding-right: 0; }
.documents-table .document-id-cell { width: 45px; color: #697586; }
.document-expand-button { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 28px; padding: 0; background: transparent; border: 0; border-radius: 4px; color: #8f9aad; }
.document-expand-button:hover { background: #e8effa; color: var(--document-blue); }
.document-expand-button svg { transform: rotate(-90deg); transition: transform .15s; }
.document-expand-button svg.expanded { transform: rotate(0deg); }
.document-title-cell { width: 32%; }
.document-name { width: 100%; padding: 0; display: flex; align-items: center; gap: 10px; background: transparent; border: 0; text-align: left; color: #38465b; }
.document-name:hover strong { color: var(--document-blue); }
.document-name > span:last-child { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.document-name strong { display: -webkit-box; /* 标准属性 */
    line-clamp: 2; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; font-size: 13px; font-weight: 500; line-height: 1.6; overflow-wrap: anywhere; }
.document-name small { color: #697586; font-size: 10px; }
.document-file-icon { display: grid; place-items: center; width: 31px; height: 37px; flex-shrink: 0; background: #edf3fc; color: #6b8dbc; border-radius: 5px; }
.document-file-icon.faq { color: #779385; background: #eff5f1; }
.document-scope { color: #697586; white-space: nowrap; font-size: 11px; }
.document-version-pill { display: inline-block; min-width: 29px; padding: 3px 6px; border: 1px solid #e4e9f1; border-radius: 4px; font-size: 11px; color: #667b99; background: #fafbfd; text-align: center; }
.document-unpublished { color: #697586; font-size: 11px; }
.document-status { display: inline-flex; align-items: center; gap: 5px; padding: 4px 7px; border: 1px solid transparent; border-radius: 4px; white-space: nowrap; font-size: 10px; }
.document-status i { width: 4px; height: 4px; border-radius: 50%; background: currentColor; }
.document-status.published { color: #2f6f4e; background: #eef8f1; border-color: #e1f0e6; }
.document-status.draft { color: #856124; background: #fff8e9; border-color: #f5e9cd; }
.document-status.archived { color: #738198; background: #f0f3f8; border-color: #e6eaf1; }
.document-status.disabled { color: #616b7a; background: #f3f4f6; border-color: #e8ebee; }
.document-status.pending {
  color: #856124;
  background: #fff8e9;
}

.document-status.processing {
  color: #356fd1;
  background: #edf3fc;
}

.document-status.completed {
  color: #2f6f4e;
  background: #eef8f1;
}

.document-status.failed {
  color: #ad5151;
  background: #fdf0f0;
}
.document-date { color: #697586; white-space: nowrap; font-size: 11px !important; font-variant-numeric: tabular-nums; }
.document-timezone { color: #a5adba; font-size: 9px; }
.document-actions-cell { min-width: 174px; }
.document-row-actions { display: flex; align-items: center; gap: 12px; white-space: nowrap; }
.document-action-button { display: inline-flex; align-items: center; gap: 5px; min-height: 32px; }
.document-status.pending { color: #856124; background: #fff8e9; }
.document-status.processing { color: #356fd1; background: #edf3fc; }
.document-status.completed { color: #2f6f4e; background: #eef8f1; }
.document-status.failed { color: #ad5151; background: #fdf0f0; }
.document-link { padding: 3px 0; border: 0; background: transparent; font-size: 11px; color: var(--document-blue); }
.document-link:hover { text-decoration: underline; text-underline-offset: 3px; }
.document-link.warning { color: #8d682c; }
.document-link.success { color: #398261; }
.document-link.danger { color: #ad5151; }
.document-row-disabled .document-name { color: #8993a1; }
.document-row-expanded { background: #f7faff !important; }
.document-expanded-row > td { padding: 0 18px 20px 45px; background: #f7faff; }
.document-versions { border: 1px solid #e3eaf4; border-radius: 6px; overflow: hidden; background: #fff; }
.document-versions-heading { display: flex; justify-content: space-between; align-items: center; padding: 13px 16px; border-bottom: 1px solid #eaf0f8; font-size: 11px; }
.document-versions-heading > span:first-child { display: flex; align-items: center; gap: 7px; color: #5f7594; font-weight: 500; }
.document-versions-heading small, .document-versions-heading > span:last-child { color: #93a0b2; font-weight: 400; font-size: 10px; }
.document-versions-heading small { margin-left: 4px; }
.document-versions-table { width: 100%; border-collapse: collapse; }
.document-versions-table th { background: #fff; padding: 12px 16px; font-size: 10px; }
.document-versions-table td { padding: 13px 16px; font-size: 11px; }
.document-versions-table tr:last-child td { border-bottom: 0; }
.document-versions-table strong { font-weight: 500; color: #5e7291; }
.document-version-note { max-width: 260px; overflow-wrap: anywhere; color: #8490a1; }
.document-version-status { font-size: 10px; color: #7f8b9e; }
.document-version-status small { margin-left: 7px; font-size: 9px; color: #9ca8b8; }
.document-version-status.published { color: #36815b; }
.document-version-status.draft { color: #a07932; }
.document-current-version { display: inline-flex; gap: 4px; align-items: center; font-size: 10px; color: #568c6c; }
.documents-pagination { display: flex; justify-content: space-between; align-items: center; gap: 14px; padding: 20px 0; color: #8994a4; font-size: 11px; }
.documents-pagination > div { display: flex; align-items: center; gap: 7px; }
.documents-pagination select { height: 30px; margin-right: 8px; font-size: 10px; }
.document-page-button { width: 29px; height: 29px; display: inline-grid; place-items: center; border: 1px solid #e1e6ef; border-radius: 4px; background: #fff; color: #7d8da4; }
.document-page-button:hover:not(:disabled) { border-color: #9cb7e2; background: #f3f7fe; }
.document-page-button .previous { transform: rotate(90deg); }
.document-page-button .next { transform: rotate(-90deg); }
.document-page-current { padding: 0 7px; color: #5e779c; font-variant-numeric: tabular-nums; }
.documents-empty { padding: 50px 20px; display: flex; align-items: center; flex-direction: column; color: #a5b2c2; }
.documents-empty strong { margin-top: 16px; color: #687c98; font-size: 14px; font-weight: 500; }
.documents-empty p { font-size: 12px; color: #8c99aa; margin-bottom: 20px; }
.documents-load-error { display: flex; gap: 10px; align-items: center; margin-bottom: 15px; padding: 14px; color: #a06d3a; background: #fff8ee; border-radius: 5px; font-size: 12px; }
.documents-footer-note { display: flex; justify-content: space-between; gap: 12px; color: #9ba6b5; font-size: 10px; padding: 5px 2px 0; line-height: 1.7; }
.document-toast { position: fixed; z-index: 70; top: 85px; left: 50%; transform: translateX(-50%); display: flex; align-items: center; gap: 9px; max-width: calc(100vw - 32px); padding: 8px 12px 8px 16px; border: 1px solid #dce8dd; background: #fff; box-shadow: 0 5px 24px #20364d15; border-radius: 8px; color: #4d805c; font-size: 12px; line-height: 1.7; }
.document-toast.error { color: #ad7143; border-color: #efdfc8; }
.document-toast .icon-button { height: 25px; width: 25px; }
.document-dialog { width: min(580px, calc(100vw - 32px)); max-height: calc(100dvh - 48px); padding: 0; border: 1px solid #e2e8f1; border-radius: 12px; background: #fff; box-shadow: 0 20px 70px #14263d25; color: #35445a; overflow-y: auto; }
.document-dialog::backdrop { background: #172a454d; }
.document-dialog > header { display: flex; justify-content: space-between; align-items: center; padding: 23px 24px 18px; border-bottom: 1px solid #edf0f5; }
.document-dialog-eyebrow { color: #8a98ac; font-size: 10px; }
.document-dialog h2 { margin: 7px 0 0; font-size: 19px; font-weight: 600; }
.document-dialog h2 > span { color: #7890b1; font-size: 15px; }
.document-editor, .document-confirm-content, .document-preview-content { padding: 20px 24px 0; }
.document-dialog-description { margin: 0 0 20px; color: #8995a6; font-size: 12px; line-height: 1.9; overflow-wrap: anywhere; }
.document-editor label { display: block; margin: 0 0 18px; font-size: 12px; color: #5e6c81; }
.document-editor label > span { color: #bc7272; }
.document-editor input, .document-editor textarea { display: block; width: 100%; margin-top: 8px; padding: 10px 12px; border: 1px solid #dce3ed; border-radius: 5px; color: #42516a; background: #fff; font-size: 12px; line-height: 1.7; }
.document-editor textarea { resize: vertical; }
.document-editor input::placeholder, .document-editor textarea::placeholder { color: #a3adbb; }
.document-editor label small { display: block; margin-top: 7px; font-size: 10px; line-height: 1.8; color: #95a0b1; }
.document-dialog footer { display: flex; justify-content: flex-end; gap: 10px; padding: 20px 0; margin-top: 16px; border-top: 1px solid #edf0f5; }
.document-confirm-icon { display: grid; place-items: center; width: 48px; height: 48px; margin-bottom: 16px; border-radius: 50%; color: #6b87b1; background: #f0f5fc; }
.document-confirm-icon.danger { color: #bd6969; background: #fdf0f0; }
.document-confirm-content > p:not(.document-dialog-description) { font-size: 14px; line-height: 1.8; overflow-wrap: anywhere; }
.document-preview-content dl { margin: 0 0 25px; }
.document-preview-content dl > div { display: flex; gap: 20px; padding: 10px 0; font-size: 12px; }
.document-preview-content dt { min-width: 60px; color: #8998ae; }
.document-preview-content dd { margin: 0; overflow-wrap: anywhere; }
.document-preview-content h3 { font-size: 14px; line-height: 1.8; font-weight: 500; }
.document-faq-answer { font-size: 13px; line-height: 1.9; white-space: pre-wrap; overflow-wrap: anywhere; color: #728198; }
.document-file-preview { display: flex; align-items: center; flex-direction: column; gap: 14px; padding: 25px; border: 1px dashed #dce5f0; border-radius: 7px; color: #92a4bc; text-align: center; }
.document-file-preview strong { font-weight: 500; font-size: 13px; overflow-wrap: anywhere; }
.document-file-preview span { font-size: 11px; line-height: 1.8; }
.visually-hidden { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
@media (max-width: 1200px) { .documents-heading { align-items: flex-start; flex-direction: column; gap: 16px; } .document-search { max-width: none; } }
@media (max-width: 760px) { .documents-page { padding: 0 14px 22px; } .documents-topline { min-height: 60px; font-size: 11px; } .documents-topline > span { max-width: 180px; line-height: 1.7; } .documents-heading { padding: 20px 16px; } h1 { font-size: 19px; } .documents-heading p { font-size: 11px; } .documents-body { padding: 16px 14px 0; } .documents-preview-note { font-size: 10px; } .documents-filters { padding: 12px; gap: 8px; } .document-search { flex: 1 0 100%; } .documents-filters select { flex: 1; min-width: 0; } .documents-list-heading > span:last-child { max-width: 145px; text-align: right; line-height: 1.7; font-size: 10px; } .documents-pagination { flex-wrap: wrap; justify-content: flex-end; gap: 12px; } .documents-pagination > span { margin-right: auto; } .documents-footer-note { flex-direction: column; gap: 4px; } .document-toast { width: calc(100vw - 32px); } .document-dialog > header { padding: 20px 18px 16px; } .document-editor, .document-confirm-content, .document-preview-content { padding: 18px 18px 0; } }
</style>
