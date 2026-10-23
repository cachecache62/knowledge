<script setup>
import { computed, nextTick, onBeforeUnmount, onDeactivated, onMounted, reactive, ref, watch } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { currentUser } from '../state/auth.js'
import { addDraft, documentStatus, filterDocuments, latestVersion, publishedVersion, publishVersion, readDocuments, versionLabels, writeDocuments } from '../utils/documents.js'

// const documents = ref([])
// const filters = reactive({ keyword: '', kind: '', status: '' })
const filters = reactive({ keyword: '', status: '' })
const appliedFilters = reactive({ ...filters })
// const page = ref(1)
// const pageSize = ref(10)
// const expanded = ref(new Set())
// const loadError = ref('')
// const notice = ref('')
// const noticeError = ref(false)
// const dialog = ref(null)
// const firstField = ref(null)
// const modal = ref(null)
// const modalError = ref('')
// const editor = reactive({ title: '', question: '', answer: '', filename: '', note: '' })

const documents = ref([])
const total = ref(0)
const loading = ref(false)
const loadError = ref('')

const page = ref(1)
const pageSize = ref(10)


const pageCount = computed(() =>
  Math.max(1, Math.ceil(total.value / pageSize.value))
)



let noticeTimer


const statusCodes = { draft: 'DRAFT', published: 'PUBLISHED', archived: 'ARCHIVED', disabled: 'DISABLED' }
const filtered = computed(() => filterDocuments(documents.value, appliedFilters).sort((a, b) => b.id - a.id))
// const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize.value)))
// const visibleDocuments = computed(() => filtered.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))

// 后端已经分页，这里直接展示接口返回的当前页数据。
const visibleDocuments = computed(() => documents.value)

const statusLabels = {
  pending: '待处理',
  processing: '处理中',
  completed: '已完成',
  failed: '失败',
}

// 你之前注释了这个函数，需要恢复。
function formatDate(value) {
  if (!value) return '—'

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'

  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  })
}

const hasFilters = computed(() => Object.values(appliedFilters).some(Boolean))
const modalDocument = computed(() => documents.value.find(document => document.id === modal.value?.documentId))
const editingFaq = computed(() => modal.value?.type === 'faq' || modalDocument.value?.kind === 'faq')
const modalTitle = computed(() => {
  if (modal.value?.type === 'faq') return '新增 FAQ'
  if (modal.value?.type === 'editor') return modal.value.versionNumber ? '编辑草稿' : '新建版本'
  if (modal.value?.type === 'preview') return '版本详情'
  return modal.value?.action === 'delete' ? '删除文档' : modal.value?.action === 'publish' ? '发布版本' : '回滚版本'
})
const previewVersion = computed(() => modalDocument.value?.versions.find(version => version.number === modal.value?.versionNumber))

watch([pageCount, pageSize], () => { page.value = Math.min(page.value, pageCount.value) })

function notify(message, error = false) {
  clearTimeout(noticeTimer)
  notice.value = message
  noticeError.value = error
  noticeTimer = setTimeout(() => { notice.value = '' }, 5000)
}

function loadList1(showNotice = false) {
  try {
    documents.value = readDocuments(currentUser.value.id)
    loadError.value = ''
    if (showNotice) notify('已刷新本地预览记录。')
  } catch {
    loadError.value = '无法读取本地预览记录，请检查浏览器存储权限或重新加载。'
  }
}

// 读取真实数据
// import {
//   computed,
//   onActivated,
//   onBeforeUnmount,
//   onDeactivated,
//   reactive,
//   ref,
// } from 'vue'
// import { IMPORT_API_BASE, requestJson } from '../api.js'



import {
  // computed,
  onActivated,
  // onBeforeUnmount,
  // onDeactivated,
  // reactive,
  // ref,
} from 'vue'
import { IMPORT_API_BASE, requestJson } from '../api.js'


let activeRequest

async function loadList() {
  activeRequest?.abort()

  const controller = new AbortController()
  activeRequest = controller
  loading.value = true
  loadError.value = ''

  const params = new URLSearchParams({
    page: String(page.value),
    page_size: String(pageSize.value),
    keyword: filters.keyword.trim(),
  })

  if (filters.status) params.set('status', filters.status)

  try {
    const data = await requestJson(
      IMPORT_API_BASE,
      `/documents?${params}`,
      { signal: controller.signal }
    )

    if (activeRequest !== controller) return

    documents.value = data.items
    total.value = data.total
  } catch (error) {
    if (activeRequest === controller && !controller.signal.aborted) {
      loadError.value = `加载文档失败：${error.message}`
    }
  } finally {
    if (activeRequest === controller) loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  loadList()
}

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  applyFilters()
}

function changePage(value) {
  page.value = value
  loadList()
}

function changePageSize() {
  page.value = 1
  loadList()
}

// 页面被 KeepAlive 缓存，返回管理页时重新获取真实数据。
onActivated(loadList)
onDeactivated(() => activeRequest?.abort())
onBeforeUnmount(() => activeRequest?.abort())



</script>

<template>
  <main class="documents-page">
    <div class="documents-topline"><span>管理知识内容，让每一次回答都有据可依。</span><button class="document-button" @click="loadList(true)"><AppIcon name="refresh" :size="15" />刷新列表</button></div>

    <section class="documents-panel" aria-labelledby="documents-title">
      <header class="documents-heading">
        <div><div class="documents-title-line"><h1 id="documents-title">知识文档与版本</h1><span class="documents-preview-label">本地预览</span></div><p>统一管理上传文档与 FAQ。通过新建版本更新内容，保留历史记录，随时查看与回滚。</p></div>
        <div class="documents-heading-actions"><button class="document-button" @click="openEditor()"><AppIcon name="plus" :size="16" />新增 FAQ</button><RouterLink to="/import" class="document-button primary"><AppIcon name="upload" :size="16" />上传文档</RouterLink></div>
      </header>

      <div class="documents-body">
        <div class="documents-preview-note"><AppIcon name="info" :size="16" /><span>示例数据与管理操作仅保存在当前浏览器，不影响实际知识库；上传文档进入现有导入流程。</span></div>
        <form class="documents-filters" @submit.prevent="applyFilters">
          <label class="document-search"><AppIcon name="search" :size="17" /><input v-model="filters.keyword" aria-label="搜索文档标题、ID或状态" placeholder="搜索文档标题 / ID / 状态" /></label>
          <select v-model="filters.kind" aria-label="文档类型"><option value="">全部类型</option><option value="document">上传文档</option><option value="faq">FAQ</option></select>
          <select v-model="filters.status" aria-label="文档状态"><option value="">全部状态</option><option value="draft">草稿</option><option value="published">已发布</option><option value="archived">已归档</option><option value="disabled">已停用</option></select>
          <button type="submit" class="document-button primary">查询</button><button type="button" class="document-button" @click="resetFilters">重置</button>
        </form>

        <div class="documents-list-heading"><span>全部文档 <strong>{{ documents.length }}</strong></span><span>{{ hasFilters ? `筛选结果 ${filtered.length} 条` : '点击文档左侧箭头，查看版本历史' }}</span></div>
        <div v-if="loadError" class="documents-load-error" role="alert"><AppIcon name="info" :size="18" /><span>{{ loadError }}</span><button class="document-link" @click="loadList()">重新加载</button></div>
        <div class="documents-table-scroll" role="region" aria-label="知识库文档列表" tabindex="0">
          <table class="documents-table">
            <thead>
  <tr>
    <th scope="col">任务 ID</th>
    <th scope="col" class="document-title-cell">文档标题</th>
    <th scope="col">适用范围</th>
    <th scope="col">导入状态</th>
    <th scope="col">创建时间（北京）</th>
    <th scope="col">更新时间（北京）</th>
  </tr>
</thead>

<tbody>
  <tr
    v-for="document in visibleDocuments"
    :key="document.task_id"
  >
    <td
      class="document-id-cell"
      :title="document.task_id"
    >
      {{ document.task_id.slice(0, 8) }}
    </td>

    <td>
      <div class="document-name" :title="document.filename">
        <span class="document-file-icon">
          <AppIcon name="file" :size="18" />
        </span>

        <span>
          <strong>{{ document.filename }}</strong>
          <small>
            文档 ·
            {{ (document.filename.split('.').pop() || '').toUpperCase() }}
          </small>
        </span>
      </div>
    </td>

    <td>
      <span class="document-scope">
        {{ document.scope === 'shared_kb' ? '共享知识库' : document.scope }}
      </span>
    </td>

    <td>
      <span
        class="document-status"
        :class="document.status"
      >
        <i></i>
        {{ statusLabels[document.status] || document.status }}
      </span>
    </td>

    <td class="document-date">
      {{ formatDate(document.created_at) }}
    </td>

    <td class="document-date">
      {{ formatDate(document.updated_at) }}
    </td>
  </tr>

  <tr v-if="!visibleDocuments.length">
    <td colspan="6">
      <div class="documents-empty">
        <AppIcon name="file" :size="30" />

        <strong>
          {{
            loading
              ? '正在加载文档…'
              : loadError
                ? '文档加载失败'
                : '暂无符合条件的文档'
          }}
        </strong>
      </div>
    </td>
  </tr>
</tbody>
          </table>
        </div>

        <footer class="documents-pagination"><span>共 {{ filtered.length }} 条{{ filtered.length ? `，显示 ${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, filtered.length)} 条` : '' }}</span><div><select v-model.number="pageSize" aria-label="每页条数" @change="page = 1"><option :value="5">5 条 / 页</option><option :value="10">10 条 / 页</option><option :value="20">20 条 / 页</option></select><button class="document-page-button" :disabled="page <= 1" aria-label="上一页" @click="page--"><AppIcon name="chevron" :size="15" class="previous" /></button><span class="document-page-current" aria-live="polite">{{ page }} / {{ pageCount }}</span><button class="document-page-button" :disabled="page >= pageCount" aria-label="下一页" @click="page++"><AppIcon name="chevron" :size="15" class="next" /></button></div></footer>
      </div>
    </section>
    <p class="documents-footer-note">内容有版本，知识可追溯。<span>当前展示示例数据，实际上传记录需后续接入文档列表接口。</span></p>

    <div v-if="notice" class="document-toast" :class="{ error: noticeError }" :role="noticeError ? 'alert' : 'status'"><AppIcon :name="noticeError ? 'info' : 'check'" :size="17" /><span>{{ notice }}</span><button class="icon-button" aria-label="关闭提示" @click="notice = ''"><AppIcon name="close" :size="15" /></button></div>

    <dialog ref="dialog" class="document-dialog" aria-labelledby="document-dialog-title" @close="modal = null" @cancel="closeModal">
      <template v-if="modal"><header><div><span class="document-dialog-eyebrow">资料库 · 本地预览</span><h2 id="document-dialog-title">{{ modalTitle }}<span v-if="modal.versionNumber"> V{{ modal.versionNumber }}</span></h2></div><button type="button" class="icon-button" aria-label="关闭弹窗" @click="closeModal"><AppIcon name="close" /></button></header>
        <form v-if="modal.type === 'faq' || modal.type === 'editor'" class="document-editor" @submit.prevent="saveEditor">
          <p class="document-dialog-description">{{ modal.type === 'faq' ? '整理一个常见问题，保存为草稿后可预览发布。' : `为「${modalDocument?.title}」${modal.versionNumber ? '编辑当前草稿' : '创建新版本，当前发布版本保持不变'}。` }}</p>
          <template v-if="editingFaq"><label>文档标题 <span>*</span><input ref="firstField" v-model="editor.title" required maxlength="200" placeholder="例如：产品保修与售后服务" /></label><label>问题 <span>*</span><input v-model="editor.question" required maxlength="500" placeholder="输入用户可能提出的问题" /></label><label>回答 <span>*</span><textarea v-model="editor.answer" required maxlength="20000" rows="6" placeholder="输入完整、清晰的回答内容"></textarea></label></template>
          <label v-else>版本文件名称 <span>*</span><input ref="firstField" v-model="editor.filename" required maxlength="254" placeholder="例如：设备用户手册-v2.pdf" /><small>本地预览仅记录文件名；实际文件请通过「上传文档」导入。</small></label>
          <label>更新说明<textarea v-model="editor.note" rows="2" maxlength="500" placeholder="简要说明本次更新的内容"></textarea></label>
          <p v-if="modalError" class="message-error" role="alert">{{ modalError }}</p><footer><button type="button" class="document-button" @click="closeModal">取消</button><button type="submit" class="document-button primary">保存草稿</button></footer>
        </form>
        <div v-else-if="modal.type === 'preview'" class="document-preview-content"><p class="document-dialog-description">{{ modalDocument?.title }}</p><dl><div><dt>版本状态</dt><dd>{{ versionLabels[previewVersion?.status] }}</dd></div><div><dt>创建时间</dt><dd>{{ formatDate(previewVersion?.createdAt) }}（北京时间）</dd></div><div><dt>更新说明</dt><dd>{{ previewVersion?.note || '—' }}</dd></div></dl><template v-if="modalDocument?.kind === 'faq'"><h3>{{ previewVersion?.question }}</h3><p class="document-faq-answer">{{ previewVersion?.answer }}</p></template><div v-else class="document-file-preview"><AppIcon name="file" :size="32" /><strong>{{ previewVersion?.filename }}</strong><span>此处为版本记录预览，未关联可下载的文档文件。</span></div><footer><button class="document-button" @click="closeModal">关闭</button></footer></div>
        <div v-else class="document-confirm-content"><div class="document-confirm-icon" :class="{ danger: modal.action === 'delete' }"><AppIcon :name="modal.action === 'delete' ? 'trash' : 'history'" :size="24" /></div><p>确定{{ modal.action === 'delete' ? '删除' : modal.action === 'publish' ? '发布' : '回滚' }}「{{ modalDocument?.title }}」{{ modal.action === 'delete' ? '及其全部版本' : `的 V${modal.versionNumber} 版本` }}吗？</p><p class="document-dialog-description">{{ modal.action === 'delete' ? '只删除当前浏览器中的预览记录，不会删除服务器文件或向量数据。' : '只更新本地预览的发布状态，原发布版本将归档；不会更新实际检索索引。' }}</p><p v-if="modalError" class="message-error" role="alert">{{ modalError }}</p><footer><button ref="firstField" class="document-button" @click="closeModal">取消</button><button class="document-button" :class="modal.action === 'delete' ? 'danger-solid' : 'primary'" @click="confirmAction">{{ modal.action === 'delete' ? '删除本地记录' : modal.action === 'publish' ? '确认发布' : '确认回滚' }}</button></footer></div>
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
