<script setup>
import {
  computed,
  onActivated,
  onBeforeUnmount,
  onDeactivated,
  reactive,
  ref,
  watch,
} from "vue";
import { useRoute } from "vue-router";
import AppIcon from "../components/AppIcon.vue";
import { IMPORT_API_BASE, requestBlob, requestJson } from "../api.js";

const route = useRoute();
const taskId = computed(() => String(route.params.taskId || ""));
const items = ref([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(10);
const pageCount = computed(() =>
  Math.max(1, Math.ceil(total.value / pageSize.value)),
);
const filters = reactive({ keyword: "", status: "" });
const applied = reactive({ keyword: "", status: "" });
const detail = ref(null);
const selectedKey = ref("");
const selectedNode = computed(() =>
  detail.value?.nodes.find((node) => node.key === selectedKey.value),
);
const activeTab = ref("nodes");
const payloadTab = ref("input");
const viewMode = ref("timeline");
const loading = ref(false);
const error = ref("");
const exporting = ref(false);
const notice = ref("");
const statusLabels = {
  pending: "等待中",
  processing: "导入中",
  completed: "已完成",
  failed: "失败",
  running: "执行中",
  success: "成功",
  warning: "完成并有提示",
  skipped: "已跳过",
  unrecorded: "未记录",
};
const completedNodes = computed(
  () =>
    detail.value?.nodes.filter((node) =>
      ["success", "warning"].includes(node.status),
    ).length || 0,
);
const executedNodes = computed(
  () =>
    detail.value?.nodes.filter(
      (node) => !["skipped", "unrecorded"].includes(node.status),
    ).length || 0,
);
const selectedPayload = computed(() =>
  payloadTab.value === "error"
    ? {
        error: selectedNode.value?.error || "",
        warnings: selectedNode.value?.warnings || [],
        reason: selectedNode.value?.reason || "",
      }
    : (selectedNode.value?.[payloadTab.value] ?? null),
);
let active = false;
let request;
let exportRequest;
let refreshTimer;
let noticeTimer;

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      });
}

function duration(value) {
  if (value === null || value === undefined) return "—";
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(2)} s`;
}

function json(value) {
  return JSON.stringify(value, null, 2);
}

function notify(message) {
  clearTimeout(noticeTimer);
  notice.value = message;
  noticeTimer = setTimeout(() => {
    notice.value = "";
  }, 5000);
}

function scheduleRefresh() {
  clearTimeout(refreshTimer);
  const running = taskId.value
    ? ["pending", "processing"].includes(detail.value?.status)
    : items.value.some((item) =>
        ["pending", "processing"].includes(item.status),
      );
  if (active && running) refreshTimer = setTimeout(() => load(true), 3000);
}

async function load(silent = false) {
  clearTimeout(refreshTimer);
  request?.abort();
  const controller = new AbortController();
  request = controller;
  if (!silent) loading.value = true;
  error.value = "";
  const currentTaskId = taskId.value;
  const params = new URLSearchParams({
    page: String(page.value),
    page_size: String(pageSize.value),
    keyword: applied.keyword,
  });
  if (applied.status) params.set("status", applied.status);
  const path = currentTaskId
    ? `/import-traces/${encodeURIComponent(currentTaskId)}`
    : "/import-traces?" + params;
  try {
    const data = await requestJson(IMPORT_API_BASE, path, {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    });
    if (
      request !== controller ||
      controller.signal.aborted ||
      currentTaskId !== taskId.value
    )
      return;
    if (currentTaskId) {
      if (!Array.isArray(data.nodes)) throw new Error("导入节点记录格式不正确");
      detail.value = data;
      if (!data.nodes.some((node) => node.key === selectedKey.value)) {
        selectedKey.value =
          data.nodes.find((node) => node.status === "failed")?.key ||
          data.nodes.find((node) => node.status === "running")?.key ||
          data.nodes[0]?.key ||
          "";
        payloadTab.value =
          data.nodes.find((node) => node.key === selectedKey.value)?.status ===
          "failed"
            ? "error"
            : "input";
      }
    } else {
      if (!Array.isArray(data.items) || !Number.isInteger(data.total))
        throw new Error("导入列表格式不正确");
      total.value = data.total;
      if (page.value > pageCount.value) {
        page.value = pageCount.value;
        await load(silent);
        return;
      }
      items.value = data.items;
    }
    scheduleRefresh();
  } catch (failure) {
    if (request === controller && !controller.signal.aborted) {
      error.value =
        failure.status === 404
          ? "未找到此导入记录。历史任务可能尚无节点记录，或当前账号无权访问。"
          : "加载导入记录失败：" + failure.message;
    }
  } finally {
    if (request === controller) loading.value = false;
  }
}

function applyFilters() {
  Object.assign(applied, {
    keyword: filters.keyword.trim(),
    status: filters.status,
  });
  page.value = 1;
  load();
}

function resetFilters() {
  Object.assign(filters, { keyword: "", status: "" });
  applyFilters();
}

function changePage(value) {
  if (
    loading.value ||
    value < 1 ||
    value > pageCount.value ||
    value === page.value
  )
    return;
  page.value = value;
  load();
}

function selectNode(node) {
  selectedKey.value = node.key;
  payloadTab.value = node.status === "failed" ? "error" : "input";
}

async function copyPayload() {
  try {
    await navigator.clipboard.writeText(json(selectedPayload.value));
    notify("节点快照已复制");
  } catch {
    notify("复制失败，请在下方选择文本复制。");
  }
}

async function exportTrace() {
  if (!taskId.value || exporting.value || !detail.value) return;
  exporting.value = true;
  const exportTaskId = taskId.value;
  const controller = new AbortController();
  exportRequest = controller;
  try {
    const blob = await requestBlob(
      IMPORT_API_BASE,
      `/import-traces/${encodeURIComponent(exportTaskId)}/export`,
      {
        signal: AbortSignal.any([
          controller.signal,
          AbortSignal.timeout(15000),
        ]),
      },
    );
    if (controller.signal.aborted) return;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `import-trace-${exportTaskId}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (failure) {
    if (!controller.signal.aborted) notify("导出失败：" + failure.message);
  } finally {
    exporting.value = false;
    if (exportRequest === controller) exportRequest = undefined;
  }
}

function stopReads() {
  clearTimeout(refreshTimer);
  request?.abort();
  exportRequest?.abort();
}

watch(taskId, () => {
  exportRequest?.abort();
  detail.value = null;
  selectedKey.value = "";
  activeTab.value = "nodes";
  payloadTab.value = "input";
  if (active) load();
});
onActivated(() => {
  active = true;
  load();
});
onDeactivated(() => {
  active = false;
  stopReads();
});
onBeforeUnmount(() => {
  active = false;
  stopReads();
  clearTimeout(noticeTimer);
});
</script>

<template>
  <main class="trace-page">
    <div class="trace-toolbar">
      <div class="trace-toolbar-title">
        <RouterLink v-if="taskId" to="/import-traces" class="trace-button"
          ><AppIcon
            name="arrowRight"
            :size="15"
            class="back-icon"
          />返回列表</RouterLink
        >
        <span>{{ taskId ? "文档导入路由详情" : "文档导入路由记录" }}</span>
      </div>
      <div class="trace-actions">
        <span
          v-if="detail && ['pending', 'processing'].includes(detail.status)"
          class="auto-refresh"
          >每 3 秒自动刷新</span
        >
        <button class="trace-button" :disabled="loading" @click="load()">
          <AppIcon name="refresh" :size="15" />{{
            loading ? "刷新中…" : taskId ? "刷新详情" : "刷新列表"
          }}
        </button>
        <button
          v-if="taskId"
          class="trace-button"
          :disabled="!detail || exporting"
          @click="exportTrace"
        >
          <AppIcon name="download" :size="15" />{{
            exporting ? "导出中…" : "导出 JSON"
          }}
        </button>
      </div>
    </div>

    <div v-if="error" class="trace-error" role="alert">
      <AppIcon name="info" :size="18" /><span>{{ error }}</span
      ><button class="trace-link" @click="load()">重新加载</button>
    </div>

    <section v-if="!taskId" class="trace-panel">
      <header class="trace-heading">
        <div>
          <h1>文档导入路由记录</h1>
          <p>
            追踪文档从上传到知识入库的每一步，查看处理时间、路由选择和节点结果。
          </p>
        </div>
        <RouterLink to="/import" class="trace-button primary"
          ><AppIcon name="upload" :size="16" />上传文档</RouterLink
        >
      </header>
      <form class="trace-filters" @submit.prevent="applyFilters">
        <label class="trace-search"
          ><AppIcon name="search" :size="17" /><input
            v-model="filters.keyword"
            aria-label="搜索导入记录"
            placeholder="搜索文档名称 / 任务 ID / Trace ID"
        /></label>
        <select v-model="filters.status" aria-label="筛选导入状态">
          <option value="">全部状态</option>
          <option value="processing">导入中</option>
          <option value="completed">已完成</option>
          <option value="failed">失败</option>
        </select>
        <button type="submit" class="trace-button primary" :disabled="loading">
          查询</button
        ><button type="button" class="trace-button" @click="resetFilters">
          重置
        </button>
      </form>
      <div class="trace-table-scroll" :aria-busy="loading">
        <table class="trace-table">
          <thead>
            <tr>
              <th>文档名称 / 任务 ID</th>
              <th>导入路线</th>
              <th>状态</th>
              <th>开始时间（北京）</th>
              <th>总耗时</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.task_id">
              <td class="trace-filename">
                <RouterLink :to="'/import-traces/' + item.task_id">{{
                  item.filename
                }}</RouterLink
                ><small>{{ item.task_id }}</small>
              </td>
              <td>
                {{
                  item.route === "pdf"
                    ? "PDF → Markdown → 知识入库"
                    : "Markdown → 知识入库"
                }}
              </td>
              <td>
                <span class="trace-status" :class="item.status">{{
                  statusLabels[item.status] || item.status
                }}</span>
              </td>
              <td>{{ formatDate(item.started_at) }}</td>
              <td>{{ duration(item.duration_ms) }}</td>
              <td>
                <RouterLink
                  class="trace-link"
                  :to="'/import-traces/' + item.task_id"
                  >查看详情</RouterLink
                >
              </td>
            </tr>
            <tr v-if="!items.length">
              <td colspan="6" class="trace-empty">
                <AppIcon name="history" :size="30" /><strong>{{
                  loading
                    ? "正在加载导入记录…"
                    : error
                      ? "导入记录加载失败"
                      : "暂无导入路由记录"
                }}</strong>
                <p>
                  {{
                    applied.keyword || applied.status
                      ? "请调整筛选条件后重试。"
                      : "从功能启用后的新上传开始记录，历史任务不会补造节点数据。"
                  }}
                </p>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <footer class="trace-pagination">
        <span>共 {{ total }} 条记录</span>
        <div>
          <select
            v-model.number="pageSize"
            aria-label="每页条数"
            @change="
              page = 1;
              load();
            "
          >
            <option :value="10">10 条 / 页</option>
            <option :value="20">20 条 / 页</option>
            <option :value="50">50 条 / 页</option></select
          ><button
            class="trace-button"
            :disabled="page <= 1 || loading"
            @click="changePage(page - 1)"
          >
            上一页</button
          ><span>{{ page }} / {{ pageCount }}</span
          ><button
            class="trace-button"
            :disabled="page >= pageCount || loading"
            @click="changePage(page + 1)"
          >
            下一页
          </button>
        </div>
      </footer>
    </section>

    <section v-else-if="detail" class="trace-panel detail-panel">
      <header class="trace-heading">
        <div>
          <span class="trace-eyebrow"
            >文档导入 · {{ detail.route === "pdf" ? "PDF" : "Markdown" }}</span
          >
          <h1>{{ detail.filename }}</h1>
        </div>
        <span class="trace-status" :class="detail.status">{{
          statusLabels[detail.status] || detail.status
        }}</span>
      </header>
      <dl class="trace-overview">
        <div>
          <dt>任务 ID</dt>
          <dd>{{ detail.task_id }}</dd>
        </div>
        <div>
          <dt>导入路线</dt>
          <dd>
            {{
              detail.route === "pdf"
                ? "PDF 转换 → 内容处理 → 知识入库"
                : "Markdown 内容处理 → 知识入库"
            }}
          </dd>
        </div>
        <div>
          <dt>开始时间</dt>
          <dd>{{ formatDate(detail.started_at) }}</dd>
        </div>
        <div>
          <dt>执行节点</dt>
          <dd>{{ completedNodes }} / {{ executedNodes }} 已完成</dd>
        </div>
        <div>
          <dt>处理提示</dt>
          <dd>{{ detail.warning_count }} 条</dd>
        </div>
        <div>
          <dt>总耗时</dt>
          <dd>{{ duration(detail.duration_ms) }}</dd>
        </div>
        <div class="trace-id">
          <dt>Trace ID</dt>
          <dd>{{ detail.trace_id }}</dd>
        </div>
      </dl>
      <div class="trace-tabs" role="tablist" aria-label="导入详情分类">
        <button
          v-for="tab in [
            { key: 'nodes', label: '节点时序与流程' },
            { key: 'context', label: '文档与参数来源' },
            { key: 'result', label: '导入结果' },
          ]"
          :key="tab.key"
          role="tab"
          :aria-selected="activeTab === tab.key"
          :class="{ selected: activeTab === tab.key }"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
      </div>

      <div v-if="activeTab === 'nodes'" class="trace-node-content">
        <div class="view-toggle">
          <button
            :class="{ selected: viewMode === 'timeline' }"
            @click="viewMode = 'timeline'"
          >
            时间线</button
          ><button
            :class="{ selected: viewMode === 'flow' }"
            @click="viewMode = 'flow'"
          >
            流程图
          </button>
        </div>
        <div
          v-if="viewMode === 'flow'"
          class="trace-flow"
          aria-label="文档导入流程图"
        >
          <template v-for="(node, index) in detail.nodes" :key="node.key"
            ><span v-if="index" class="flow-arrow">→</span
            ><button
              class="flow-node"
              :class="[node.status, { selected: selectedKey === node.key }]"
              @click="selectNode(node)"
            >
              <strong>{{ node.label }}</strong
              ><small
                >{{ statusLabels[node.status] }} ·
                {{ duration(node.duration_ms) }}</small
              >
            </button></template
          >
        </div>
        <div
          class="trace-split"
          :class="{ 'flow-details': viewMode === 'flow' }"
        >
          <ol
            v-if="viewMode === 'timeline'"
            class="trace-timeline"
            aria-label="导入节点时间线"
          >
            <li
              v-for="node in detail.nodes"
              :key="node.key"
              :class="node.status"
            >
              <button
                class="timeline-card"
                :class="{ selected: selectedKey === node.key }"
                :aria-pressed="selectedKey === node.key"
                @click="selectNode(node)"
              >
                <strong>{{ node.sequence }}. {{ node.label }}</strong
                ><span
                  >{{ node.provider }} ·
                  <span :class="'node-' + node.status">{{
                    statusLabels[node.status]
                  }}</span></span
                ></button
              ><small
                >{{
                  node.started_at
                    ? formatDate(node.started_at)
                    : node.status === "pending"
                      ? "等待前序节点"
                      : node.reason || "未执行"
                }}<template v-if="node.started_at">
                  · {{ duration(node.duration_ms) }}</template
                ></small
              >
            </li>
          </ol>
          <article v-if="selectedNode" class="trace-node-detail">
            <h2>{{ selectedNode.label }}</h2>
            <p class="node-meta">
              {{ selectedNode.key }} · {{ selectedNode.provider }} ·
              <span class="trace-status" :class="selectedNode.status">{{
                statusLabels[selectedNode.status]
              }}</span>
            </p>
            <p class="node-upstream">
              上游节点：{{
                detail.nodes.find((node) => node.key === selectedNode.upstream)
                  ?.label || "无"
              }}<span>耗时：{{ duration(selectedNode.duration_ms) }}</span>
            </p>
            <p v-if="selectedNode.reason" class="trace-note">
              {{ selectedNode.reason }}
            </p>
            <p class="trace-note">{{ detail.snapshot_policy }}</p>
            <div
              class="trace-tabs payload-tabs"
              role="tablist"
              aria-label="节点快照"
            >
              <button
                v-for="tab in [
                  { key: 'input', label: '输入参数' },
                  { key: 'output', label: '输出结果' },
                  { key: 'error', label: '错误与提示' },
                ]"
                :key="tab.key"
                role="tab"
                :aria-selected="payloadTab === tab.key"
                :class="{ selected: payloadTab === tab.key }"
                @click="payloadTab = tab.key"
              >
                {{ tab.label }}
              </button>
              <button class="trace-link copy-button" @click="copyPayload">
                <AppIcon name="copy" :size="14" />复制 JSON
              </button>
            </div>
            <pre
              class="trace-json"
              tabindex="0"
              :aria-label="selectedNode.label + '节点快照'"
            ><code>{{ selectedPayload === null ? '此节点尚无快照。' : json(selectedPayload) }}</code></pre>
          </article>
        </div>
      </div>

      <div v-else-if="activeTab === 'context'" class="trace-tab-content">
        <h2>文档与参数来源</h2>
        <p class="trace-note">
          输入由上传请求产生，后续节点从上游工作流状态读取。每个节点的输入和输出均在执行时记录。
        </p>
        <dl class="trace-context">
          <div>
            <dt>文档名称</dt>
            <dd>{{ detail.filename }}</dd>
          </div>
          <div>
            <dt>上传用户</dt>
            <dd>当前账号 · {{ detail.user_id }}</dd>
          </div>
          <div>
            <dt>知识库范围</dt>
            <dd>
              {{ detail.scope === "shared_kb" ? "共享知识库" : detail.scope }}
            </dd>
          </div>
          <div>
            <dt>路线选择</dt>
            <dd>
              {{
                detail.route === "pdf"
                  ? "扩展名为 .pdf，先解析为 Markdown。"
                  : "扩展名为 .md，跳过 PDF 解析，直接处理内容。"
              }}
            </dd>
          </div>
          <div>
            <dt>快照说明</dt>
            <dd>{{ detail.snapshot_policy }}</dd>
          </div>
        </dl>
      </div>
      <div v-else class="trace-tab-content">
        <h2>导入结果</h2>
        <p>
          <span class="trace-status" :class="detail.status">{{
            statusLabels[detail.status]
          }}</span>
        </p>
        <p v-if="detail.error" class="trace-error" role="alert">
          {{ detail.error }}
        </p>
        <p v-else-if="detail.status === 'completed'">
          导入流程已完成，可在文档管理中查看文档。
        </p>
        <p v-else>任务正在处理，请等待节点执行完成。</p>
        <p class="trace-note">
          结束时间：{{ formatDate(detail.finished_at) }} · 总耗时：{{
            duration(detail.duration_ms)
          }}
          · 处理提示：{{ detail.warning_count }} 条
        </p>
        <RouterLink to="/documents" class="trace-button"
          >查看文档管理</RouterLink
        >
      </div>
    </section>
    <div v-else-if="loading" class="trace-loading" role="status">
      正在加载导入详情…
    </div>
    <div v-if="notice" class="trace-toast" role="status">{{ notice }}</div>
  </main>
</template>

<style scoped>
.trace-page {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 28px 28px;
  background: #f6f8fb;
  color: #303b4d;
  font-size: 13px;
}
.trace-toolbar,
.trace-toolbar-title,
.trace-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.trace-toolbar {
  justify-content: space-between;
  min-height: 70px;
  flex-wrap: wrap;
  padding: 10px 0;
}
.trace-toolbar-title {
  font-size: 15px;
  font-weight: 600;
}
.trace-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 34px;
  padding: 7px 12px;
  border: 1px solid #dce3ed;
  background: white;
  color: #526075;
  font: inherit;
  font-size: 12px;
  border-radius: 5px;
  text-decoration: none;
  white-space: nowrap;
}
.trace-button:hover:not(:disabled) {
  border-color: #9bb7e3;
  background: #f3f7fd;
}
.trace-button.primary {
  background: #356fd1;
  color: white;
  border-color: #356fd1;
}
button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.back-icon {
  transform: rotate(180deg);
}
.auto-refresh,
.trace-note,
.trace-eyebrow {
  color: #7a889c;
  font-size: 12px;
  line-height: 1.8;
}
.trace-panel {
  border: 1px solid #e5eaf2;
  border-radius: 9px;
  background: white;
  box-shadow: 0 2px 6px #263c5210;
  overflow: hidden;
}
.trace-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 25px 24px;
}
h1 {
  margin: 0;
  font-size: 21px;
  color: #263346;
  overflow-wrap: anywhere;
}
h2 {
  margin: 0 0 15px;
  font-size: 17px;
  color: #303b4d;
}
.trace-eyebrow {
  display: block;
  margin-bottom: 7px;
}
.trace-heading p {
  margin: 10px 0 0;
  color: #7a889c;
  font-size: 12px;
  line-height: 1.8;
}
.trace-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 0 24px 22px;
  padding: 16px;
  background: #f6f8fb;
  border-radius: 6px;
}
.trace-search {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  max-width: 420px;
  min-width: 200px;
  padding: 0 11px;
  border: 1px solid #dce3ed;
  background: white;
  border-radius: 5px;
  color: #8997aa;
}
.trace-search input {
  height: 35px;
  width: 100%;
  min-width: 0;
  border: 0;
  background: transparent;
  outline: none;
  color: #303b4d;
  font: inherit;
  font-size: 12px;
}
.trace-search:focus-within {
  border-color: #356fd1;
}
select {
  height: 35px;
  padding: 0 24px 0 10px;
  border: 1px solid #dce3ed;
  border-radius: 5px;
  background: white;
  color: #66748a;
  font: inherit;
  font-size: 12px;
}
.trace-table-scroll {
  overflow-x: auto;
  margin: 0 24px;
}
.trace-table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
  min-width: 840px;
  font-size: 12px;
}
.trace-table th {
  padding: 15px 14px;
  background: #f6f8fb;
  color: #7d8ba0;
  font-weight: 500;
  white-space: nowrap;
}
.trace-table td {
  padding: 19px 14px;
  border-bottom: 1px solid #edf0f5;
}
.trace-filename {
  max-width: 340px;
  min-width: 220px;
  overflow-wrap: anywhere;
}
.trace-filename a {
  display: block;
  color: #354963;
  text-decoration: none;
  line-height: 1.7;
  font-weight: 500;
}
.trace-filename small {
  display: block;
  font-size: 10px;
  color: #8b97a8;
  margin-top: 5px;
}
.trace-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #356fd1;
  text-decoration: none;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.trace-link:hover {
  text-decoration: underline;
}
.trace-status {
  display: inline-flex;
  padding: 4px 8px;
  background: #f0f3f8;
  border-radius: 4px;
  color: #7a879b;
  font-size: 11px;
  white-space: nowrap;
}
.trace-status.success,
.trace-status.completed {
  color: #337a55;
  background: #eef8f1;
}
.trace-status.running,
.trace-status.processing {
  color: #356fd1;
  background: #edf3ff;
}
.trace-status.failed {
  color: #ba5151;
  background: #fff0f0;
}
.trace-status.warning {
  color: #997126;
  background: #fff8e8;
}
.trace-pagination,
.trace-pagination > div {
  display: flex;
  align-items: center;
  gap: 10px;
}
.trace-pagination {
  justify-content: space-between;
  padding: 20px 24px;
  color: #8995a7;
  font-size: 12px;
}
.trace-empty {
  text-align: center;
  color: #8997aa;
  padding: 50px 16px !important;
}
.trace-empty strong {
  display: block;
  margin-top: 13px;
  font-weight: 500;
}
.trace-empty p {
  font-size: 12px;
}
.trace-overview {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 0 24px 10px;
  border-top: 1px solid #e8edf5;
  border-left: 1px solid #e8edf5;
}
.trace-overview > div {
  display: flex;
  border-right: 1px solid #e8edf5;
  border-bottom: 1px solid #e8edf5;
  min-width: 0;
}
.trace-overview dt {
  flex: 0 0 85px;
  padding: 12px;
  background: #f5f7fb;
  color: #69768b;
  font-weight: 500;
}
.trace-overview dd {
  margin: 0;
  padding: 12px;
  font-size: 12px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.trace-overview .trace-id {
  grid-column: 1 / -1;
}
.trace-tabs {
  display: flex;
  align-items: center;
  gap: 24px;
  margin: 0 24px;
  border-bottom: 1px solid #e3e8f0;
  overflow-x: auto;
}
.trace-tabs > button {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  padding: 14px 0 12px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: none;
  color: #6f7d90;
  font: inherit;
  cursor: pointer;
}
.trace-tabs > button.selected {
  color: #3978dc;
  border-bottom-color: #4f8ef5;
}
.trace-node-content {
  padding: 18px 24px 26px;
}
.view-toggle {
  display: inline-flex;
  margin-bottom: 18px;
}
.view-toggle button {
  padding: 7px 13px;
  background: white;
  color: #7a8799;
  border: 1px solid #dce3ee;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.view-toggle button:first-child {
  border-radius: 4px 0 0 4px;
}
.view-toggle button:last-child {
  border-radius: 0 4px 4px 0;
}
.view-toggle button.selected {
  background: #4b8bf2;
  border-color: #4b8bf2;
  color: white;
}
.trace-split {
  display: grid;
  grid-template-columns: minmax(240px, 34%) minmax(0, 1fr);
  gap: 26px;
}
.trace-timeline {
  margin: 0;
  padding: 0 16px 0 18px;
  list-style: none;
  max-height: 65vh;
  overflow-y: auto;
}
.trace-timeline li {
  position: relative;
  padding: 0 0 20px 22px;
  border-left: 2px solid #e5eaf3;
}
.trace-timeline li:last-child {
  border-color: transparent;
}
.trace-timeline li::before {
  content: "";
  position: absolute;
  left: -6px;
  top: 7px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #cbd4e2;
  box-shadow: 0 0 0 3px white;
}
.trace-timeline li.success::before {
  background: #4c7cf1;
}
.trace-timeline li.running::before {
  background: #4b8bf2;
  box-shadow: 0 0 0 4px #eaf1ff;
}
.trace-timeline li.failed::before {
  background: #d76b6b;
}
.trace-timeline li.warning::before {
  background: #d4a54f;
}
.timeline-card {
  width: 100%;
  text-align: left;
  padding: 12px;
  border: 1px solid #e3e9f3;
  border-radius: 7px;
  background: white;
  cursor: pointer;
  color: #3f4c60;
}
.timeline-card.selected {
  border-color: #7796ff;
  background: #f0f5ff;
}
.timeline-card strong {
  display: block;
  font-weight: 500;
  line-height: 1.7;
  font-size: 13px;
}
.timeline-card > span,
.trace-timeline li > small {
  display: block;
  color: #8694aa;
  font-size: 11px;
  margin-top: 6px;
  line-height: 1.7;
}
.node-failed {
  color: #c25959;
}
.node-warning {
  color: #b0873b;
}
.trace-node-detail {
  min-width: 0;
  padding: 4px 0;
}
.node-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  color: #6b788d;
  font-size: 12px;
  line-height: 1.8;
}
.node-upstream {
  display: flex;
  flex-wrap: wrap;
  gap: 25px;
  padding: 10px 0 14px;
  border-bottom: 1px solid #edf0f6;
  color: #7b889b;
  font-size: 12px;
}
.payload-tabs {
  margin: 0;
  gap: 20px;
}
.payload-tabs > .copy-button {
  margin-left: auto;
  font-size: 11px;
  color: #3978dc;
}
.trace-json {
  max-height: 55vh;
  overflow: auto;
  padding: 18px;
  margin: 18px 0 0;
  border-radius: 7px;
  background: #f6f8fc;
  color: #50627c;
  font:
    12px/1.8 ui-monospace,
    Consolas,
    monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.trace-flow {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
  padding: 8px 0 24px;
}
.flow-node {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  border: 1px solid #dfe6f1;
  border-radius: 7px;
  background: #fafcff;
  color: #52647e;
  text-align: left;
  cursor: pointer;
}
.flow-node strong {
  font-size: 12px;
  font-weight: 500;
}
.flow-node small {
  font-size: 10px;
  color: #8592a5;
}
.flow-node.selected {
  border-color: #7796ff;
  background: #edf4ff;
}
.flow-node.skipped {
  opacity: 0.6;
  border-style: dashed;
}
.flow-node.failed {
  border-color: #dfa3a3;
  background: #fff7f7;
}
.flow-arrow {
  color: #a8b6ca;
}
.flow-details {
  grid-template-columns: 1fr;
}
.trace-tab-content {
  padding: 26px 24px;
  line-height: 1.8;
}
.trace-context {
  margin: 20px 0;
}
.trace-context > div {
  display: flex;
  border-bottom: 1px solid #edf0f5;
  padding: 12px 0;
  gap: 15px;
}
.trace-context dt {
  flex: 0 0 95px;
  color: #7c899b;
}
.trace-context dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.trace-error {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 18px;
  padding: 14px 16px;
  border: 1px solid #f0dcdc;
  border-radius: 6px;
  color: #b66357;
  background: #fff7f4;
  line-height: 1.8;
}
.trace-loading {
  padding: 70px 20px;
  text-align: center;
  color: #8997aa;
}
.trace-toast {
  position: fixed;
  z-index: 75;
  top: 85px;
  left: 50%;
  transform: translateX(-50%);
  max-width: calc(100vw - 32px);
  padding: 12px 20px;
  background: white;
  border: 1px solid #dfe6f1;
  border-radius: 8px;
  box-shadow: 0 5px 24px #20364d20;
}
@media (max-width: 1100px) {
  .trace-overview {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .trace-split {
    grid-template-columns: minmax(230px, 38%) minmax(0, 1fr);
    gap: 18px;
  }
  .trace-split.flow-details {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 760px) {
  .trace-page {
    padding: 0 14px 20px;
  }
  .trace-toolbar {
    gap: 10px;
  }
  .trace-toolbar-title {
    font-size: 13px;
  }
  .auto-refresh {
    display: none;
  }
  .trace-heading {
    padding: 20px 16px;
    align-items: flex-start;
  }
  h1 {
    font-size: 18px;
  }
  .trace-filters {
    margin: 0 16px 16px;
  }
  .trace-search {
    flex-basis: 100%;
    max-width: none;
  }
  .trace-table-scroll {
    margin: 0 16px;
  }
  .trace-overview {
    margin: 0 16px;
    grid-template-columns: 1fr;
  }
  .trace-overview dt {
    flex-basis: 80px;
  }
  .trace-tabs {
    margin: 0 16px;
    gap: 20px;
  }
  .trace-node-content {
    padding: 16px;
  }
  .trace-split {
    grid-template-columns: 1fr;
  }
  .trace-timeline {
    max-height: 330px;
    padding-right: 5px;
  }
  .payload-tabs {
    margin: 0;
  }
  .trace-pagination {
    padding: 16px;
    flex-wrap: wrap;
  }
  .trace-pagination > div {
    flex-wrap: wrap;
    gap: 7px;
  }
  .trace-tab-content {
    padding: 22px 16px;
  }
}
</style>
