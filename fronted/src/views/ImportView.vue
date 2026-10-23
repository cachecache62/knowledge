<script setup>
import { onBeforeUnmount, reactive, ref } from "vue";
import { IMPORT_API_BASE, requestJson } from "../api.js";
import AppIcon from "../components/AppIcon.vue";

const fileInput = ref(null);
const files = ref([]);
const dragging = ref(false);
const lifetime = new AbortController();
const timers = new Map();
let disposed = false;

function statusText(status) {
  return (
    {
      uploading: "上传中…",
      processing: "处理中…",
      completed: "已完成",
      failed: "失败",
      duplicate: "重复文档",
    }[status] || "等待中"
  );
}

function stopPolling(item) {
  clearTimeout(timers.get(item.id));
  timers.delete(item.id);
}

async function pollStatus(item) {
  if (disposed) return;
  try {
    const data = await requestJson(
      IMPORT_API_BASE,
      `/status/${encodeURIComponent(item.taskId)}`,
      {
        signal: AbortSignal.any([lifetime.signal, AbortSignal.timeout(10000)]),
      },
    );
    if (disposed) return;
    item.failures = 0;
    item.error = "";
    item.done = Array.isArray(data.done_list) ? data.done_list : [];
    item.running = Array.isArray(data.running_list) ? data.running_list : [];
    if (data.status === "completed") {
      item.status = "completed";
      item.progress = 100;
      stopPolling(item);
      return;
    }
    if (data.status === "failed") {
      item.status = "failed";
      item.error = data.error || "文档处理失败，请查看后端日志后重新上传。";
      stopPolling(item);
      return;
    }
    item.status = "processing";
    item.progress = Math.max(
      30,
      Math.min(95, Math.round((item.done.length / 8) * 100)),
    );
  } catch (error) {
    if (disposed) return;
    item.failures += 1;
    item.error = `查询进度失败：${error.message}`;
    if (item.failures >= 5) {
      item.pollingPaused = true;
      stopPolling(item);
      return;
    }
  }
  // 上一轮响应后再安排下一轮，避免慢请求重叠。
  if (!disposed)
    timers.set(
      item.id,
      setTimeout(() => pollStatus(item), 2000),
    );
}

function retryPolling(item) {
  item.pollingPaused = false;
  item.failures = 0;
  item.error = "";
  stopPolling(item);
  pollStatus(item);
}

async function uploadFile(file) {
  const item = reactive({
    id: crypto.randomUUID(),
    name: file.name,
    size: file.size,
    status: "uploading",
    taskId: "",
    progress: 0,
    done: [],
    running: [],
    error: "",
    failures: 0,
    pollingPaused: false,
    notice: "", // 用于显示重复文件的提示信息
  });
  files.value.unshift(item);
  // 入口按小写后缀判断；不接受后端无法处理的大写扩展名。
  if (!/\.(pdf|md)$/.test(file.name)) {
    item.status = "failed";
    item.error = "请选择扩展名为小写 .pdf 或 .md 的文件。";
    return;
  }
  const body = new FormData();
  body.append("files", file);
  try {
    const data = await requestJson(IMPORT_API_BASE, "/upload", {
      method: "POST",
      body,
      signal: lifetime.signal,
    });
    if (disposed) return;
    
    // 当前前端每次请求上传一个文件，因此取第一项。
    const result = data.results?.[0]

    // const taskId = Array.isArray(data.task_ids) ? data.task_ids[0] : null;

    if (result?.duplicate) {
      item.status = "duplicate";
      item.progress = 100;
      item.notice = `${result.message}（${result.existing_filename}）`;

      // 重复文件没有新任务，不再查询进度。
      return;
    }

    if(result?.status == "failed"){
      throw new Error(result?.message || "上传失败，请查看后端日志后重新上传。");
    }

    const taskId = result?.task_id || data.task_ids?.[0]


    if (!taskId) throw new Error("服务端未返回任务 ID");

    item.taskId = taskId;
    item.status = "processing";
    item.progress = 30;
    pollStatus(item);
  } catch (error) {
    if (disposed) return;
    item.status = "failed";
    item.error = `上传失败：${error.message}`;
  }
}

function handleFiles(list) {
  Array.from(list || []).forEach(uploadFile);
}

function selectFiles(event) {
  handleFiles(event.target.files);
  event.target.value = "";
}

function dropFiles(event) {
  dragging.value = false;
  handleFiles(event.dataTransfer.files);
}

function doneLog(name) {
  const text = String(name);
  return text.endsWith("已完成") ? text : `${text}已完成`;
}

function runningLog(name) {
  const text = String(name);
  return text.startsWith("正在进行") ? text : `正在进行${text}…`;
}

onBeforeUnmount(() => {
  disposed = true;
  lifetime.abort();
  for (const timer of timers.values()) clearTimeout(timer);
  timers.clear();
});
</script>

<template>
  <main class="import-container">
    <div class="import-heading">
      <span class="section-eyebrow">你的专属知识空间</span>
      <h1>把资料，变成随时可用的知识。</h1>
      <p>上传文档，掌柜智库会为你整理内容，在对话中找到答案。</p>
    </div>
    <div
      class="upload-area"
      :class="{ dragging }"
      role="button"
      tabindex="0"
      aria-label="点击或拖拽 PDF、Markdown 文件上传"
      @click="fileInput.click()"
      @keydown.enter.prevent="fileInput.click()"
      @keydown.space.prevent="fileInput.click()"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="dropFiles"
    >
      <div class="upload-icon" aria-hidden="true">
        <AppIcon name="upload" :size="28" />
      </div>
      <p>拖拽文件到这里，或<span class="upload-link">点击选择</span></p>
      <span class="muted">支持 PDF、Markdown，可同时上传多个文件</span>
    </div>
    <input
      ref="fileInput"
      class="file-input"
      type="file"
      multiple
      accept=".pdf,.md"
      @change="selectFiles"
    />
    <div class="file-list" aria-live="polite">
      <div class="file-list-heading">
        <h2>导入记录</h2>
        <span>{{ files.length }} 个文件 · 当前页面会话</span>
      </div>
      <div v-if="!files.length" class="empty-state">
        <AppIcon name="file" :size="28" />
        <p>还没有导入资料</p>
        <span>从一份文档开始，建立你的知识库。</span>
      </div>
      <article v-for="item in files" :key="item.id" class="file-item">
        <div class="file-info">
          <span class="file-name">{{ item.name }}</span>
          <span class="file-size">{{ (item.size / 1024).toFixed(2) }} KB</span>
          <div
            class="progress-bar-container"
            :class="{ indeterminate: item.status === 'uploading' }"
            role="progressbar"
            aria-label="导入进度"
            :aria-valuenow="
              item.status === 'uploading' ? undefined : item.progress
            "
            aria-valuemin="0"
            aria-valuemax="100"
          >
            <div
              class="progress-bar"
              :class="item.status"
              :style="{
                width:
                  item.status === 'uploading' ? '40%' : `${item.progress}%`,
              }"
            ></div>
          </div>
          <details class="log-details">
            <summary>
              日志（已完成{{ item.done.length }}，进行中{{
                item.running.length
              }}）
            </summary>
            <ul class="log-list">
              <li v-for="(name, index) in item.done" :key="`done-${index}`">
                {{ doneLog(name) }}
              </li>
              <li
                v-for="(name, index) in item.running"
                :key="`running-${index}`"
              >
                {{ runningLog(name) }}
              </li>
              <li v-if="!item.done.length && !item.running.length">暂无日志</li>
            </ul>
          </details>
          <p v-if="item.error" class="message-error" role="alert">
            {{ item.error }}
          </p>
          <!-- 重复文档等提示 -->
          <p v-if="item.notice" role="status">
            {{ item.notice }}
          </p>
          <button
            v-if="item.pollingPaused"
            class="btn retry-button"
            @click="retryPolling(item)"
          >
            重试查询进度
          </button>
        </div>
        <span class="status-badge" :class="`status-${item.status}`">{{
          item.pollingPaused ? "查询暂停" : statusText(item.status)
        }}</span>
      </article>
    </div>
  </main>
</template>
