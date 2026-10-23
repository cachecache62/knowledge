<script setup>
import { computed, nextTick, onActivated, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { QUERY_API_BASE, requestJson } from '../api.js'
import AnswerContent from '../components/AnswerContent.vue'
import TaskProgress from '../components/TaskProgress.vue'
import { connectQueryStream } from '../utils/queryStream.js'
import AppIcon from '../components/AppIcon.vue'
import { activeSessionId, conversationBusy, conversations, rememberConversation, forgetConversation } from '../state/conversations.js'
import { chatToolbar } from '../state/chatToolbar.js'

const chatElement = ref(null)
const inputElement = ref(null)
const query = ref('')
const isStream = ref(true)
const sending = ref(false)
const loadingHistory = ref(true)
const clearing = ref(false)
const connected = ref(false)
const notice = ref('')
const messages = ref([])
const copiedMessage = ref('')
const currentTitle = computed(() => conversations.value.find(item => item.id === activeSessionId.value)?.title || '新对话')
const lifetime = new AbortController()
let disposed = false
let healthTimer
let healthChecking = false
let closeStream
let historyVersion = 0
let copyTimer

let sessionId = activeSessionId.value
const toolbarState = reactive({
  connected, clearing,
  disabled: computed(() => sending.value || loadingHistory.value || clearing.value || !messages.value.length),
  clear: clearHistory,
})

watch([sending, clearing], ([isSending, isClearing]) => { conversationBusy.value = isSending || isClearing }, { flush: 'sync' })
watch(activeSessionId, (id) => {
  sessionId = id
  query.value = ''
  notice.value = ''
  messages.value = []
  loadHistory()
})
watch(query, async () => {
  await nextTick()
  resizeInput()
})

function resizeInput() {
  if (inputElement.value) {
    inputElement.value.style.height = 'auto'
    inputElement.value.style.height = `${Math.min(inputElement.value.scrollHeight, 180)}px`
    inputElement.value.style.overflowY = inputElement.value.scrollHeight > 180 ? 'auto' : 'hidden'
  }
}

function useSuggestion(text) {
  query.value = text
  inputElement.value?.focus()
}

async function copyMessage(message) {
  try {
    await navigator.clipboard.writeText(message.text)
    copiedMessage.value = message.id
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copiedMessage.value = '' }, 2000)
  } catch { notice.value = '复制失败，请选择回答文字后手动复制。' }
}

function formatTime(timestamp) {
  const date = new Date(Number(timestamp) * 1000)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit' })
}

async function scrollToBottom() {
  await nextTick()
  const element = chatElement.value
  if (element) element.scrollTop = element.scrollHeight
}

async function checkHealth() {
  if (healthChecking || disposed) return
  healthChecking = true
  try {
    await requestJson(QUERY_API_BASE, '/health', {
      signal: AbortSignal.any([lifetime.signal, AbortSignal.timeout(5000)]),
    })
    connected.value = true
  } catch {
    if (!disposed) connected.value = false
  } finally {
    healthChecking = false
  }
}

async function loadHistory() {
  const version = ++historyVersion
  loadingHistory.value = true
  try {
    const data = await requestJson(QUERY_API_BASE, `/history/${encodeURIComponent(sessionId)}`, { signal: AbortSignal.any([lifetime.signal, AbortSignal.timeout(10000)]) })
    if (disposed || version !== historyVersion) return
    const items = Array.isArray(data.items) ? data.items : []
    messages.value = items.filter((item) => ['user', 'assistant'].includes(item.role)).map((item) => ({
      id: item._id || crypto.randomUUID(), role: item.role, text: item.text || '',
      timestamp: item.ts, imageUrls: Array.isArray(item.image_urls) ? item.image_urls : [],
    }))
    const firstQuestion = messages.value.find(item => item.role === 'user')
    if (firstQuestion) rememberConversation(firstQuestion.text)
    scrollToBottom()
  } catch (error) {
    if (!disposed && version === historyVersion) notice.value = `历史记录加载失败：${error.message}`
  } finally {
    if (version === historyVersion) loadingHistory.value = false
  }
}

function finish(message) {
  message.pending = false
  sending.value = false
  if (closeStream) closeStream()
  closeStream = undefined
  scrollToBottom()
}

function fail(message, error) {
  message.status = 'failed'
  message.running = []
  message.error = error.message || '请求失败，请重试'
  finish(message)
}

async function send() {
  const text = query.value.trim()
  if (!text || sending.value || loadingHistory.value || clearing.value) return
  notice.value = ''
  query.value = ''
  sending.value = true
  rememberConversation(text)
  const streaming = isStream.value
  messages.value.push({ id: crypto.randomUUID(), role: 'user', text, timestamp: Date.now() / 1000, imageUrls: [] })
  const message = reactive({
    id: crypto.randomUUID(), role: 'assistant', text: '', timestamp: Date.now() / 1000,
    imageUrls: [], pending: true, status: 'pending', done: [], running: [], error: '',
  })
  messages.value.push(message)
  scrollToBottom()
  try {
    const data = await requestJson(QUERY_API_BASE, '/query', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: text, session_id: sessionId, is_stream: streaming }),
      signal: lifetime.signal,
    })
    if (disposed) return
    if (data.error) throw new Error(data.error)
    if (typeof data.session_id === 'string' && data.session_id) {
      sessionId = data.session_id
    }
    if (!streaming) {
      message.text = data.answer || ''
      message.imageUrls = Array.isArray(data.image_urls) ? data.image_urls : []
      message.done = Array.isArray(data.done_list) ? data.done_list : []
      message.status = 'completed'
      finish(message)
      return
    }
    if (!data.session_id) throw new Error('服务端未返回会话 ID，无法建立流式连接')
    message.status = 'processing'
    closeStream = connectQueryStream(`${QUERY_API_BASE}/stream/${encodeURIComponent(sessionId)}`, {
      onProgress(progress) {
        message.done = Array.isArray(progress.done_list) ? progress.done_list : []
        message.running = Array.isArray(progress.running_list) ? progress.running_list : []
        message.status = progress.status || 'processing'
        scrollToBottom()
      },
      onDelta(delta) {
        message.text += delta
        scrollToBottom()
      },
      onFinal(result) {
        if (typeof result.answer === 'string' && result.answer.trim()) message.text = result.answer
        message.imageUrls = Array.isArray(result.image_urls) ? result.image_urls : []
        message.status = 'completed'
        message.running = []
        finish(message)
      },
      onError(error) { fail(message, error) },
    })
  } catch (error) {
    if (!disposed) fail(message, error)
  }
}

function handleKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    send()
  }
}

async function clearHistory() {
  if (sending.value || clearing.value || loadingHistory.value) return
  if (!window.confirm('确定要清空当前会话的历史记录吗？这将无法恢复。')) return
  clearing.value = true
  notice.value = ''
  try {
    await requestJson(QUERY_API_BASE, `/history/${encodeURIComponent(sessionId)}`, { method: 'DELETE', signal: lifetime.signal })
    if (!disposed) {
      messages.value = []
      forgetConversation()
    }
  } catch (error) {
    if (!disposed) notice.value = `清空失败：${error.message}`
  } finally {
    clearing.value = false
  }
}

onMounted(() => {
  chatToolbar.value = toolbarState
  checkHealth()
  loadHistory()
  healthTimer = setInterval(checkHealth, 5000)
  window.addEventListener('resize', resizeInput)
})
onActivated(() => { chatToolbar.value = toolbarState; inputElement.value?.focus() })
onBeforeUnmount(() => {
  disposed = true
  if (chatToolbar.value === toolbarState) chatToolbar.value = null
  clearInterval(healthTimer)
  clearTimeout(copyTimer)
  window.removeEventListener('resize', resizeInput)
  conversationBusy.value = false
  lifetime.abort()
  closeStream?.()
})
</script>

<template>
  <main class="chat-app" :class="{ 'chat-empty': !messages.length }">
    <div v-if="notice" class="notice" role="alert"><span>{{ notice }}</span><button class="icon-button" aria-label="关闭提示" @click="notice = ''"><AppIcon name="close" :size="15" /></button></div>
    <div ref="chatElement" class="chat" role="log" aria-label="聊天记录" aria-live="polite" aria-relevant="additions text">
      <div v-if="!messages.length" class="chat-welcome">
        <div class="welcome-symbol"><AppIcon name="sparkle" :size="31" /></div>
        <p class="welcome-eyebrow">每一个好问题，都值得一个好答案</p>
        <h1>今天，想了解些什么？</h1>
        <p class="welcome-description">连接你的资料，在对话中发现知识。</p>
        <div class="suggestions">
          <button @click="useSuggestion('请帮我总结知识库中相关产品的主要功能。')"><AppIcon name="file" :size="18" /><span>总结文档重点</span><AppIcon name="arrowRight" :size="15" /></button>
          <button @click="useSuggestion('请根据知识库，帮我梳理产品的使用步骤。')"><AppIcon name="library" :size="18" /><span>梳理使用步骤</span><AppIcon name="arrowRight" :size="15" /></button>
          <button @click="useSuggestion('使用产品时遇到故障，我应该如何排查？')"><AppIcon name="chat" :size="18" /><span>查找问题答案</span><AppIcon name="arrowRight" :size="15" /></button>
        </div>
        <span v-if="loadingHistory" class="history-loading">正在加载历史记录…</span>
      </div>
      <div v-else class="message-thread">
        <div class="dayline"><span>{{ currentTitle }}</span></div>
        <article v-for="message in messages" :key="message.id" class="msg" :class="message.role === 'user' ? 'user' : 'bot'">
          <div class="message-content">
            <div class="bubble">
              <div v-if="message.role === 'user'" class="answer-text">{{ message.text }}</div>
              <template v-else>
                <AnswerContent :text="message.text" :image-urls="message.imageUrls" :pending="message.pending" />
                <span v-if="message.pending && !message.text" class="typing" aria-label="正在处理"><span class="dot"></span><span class="dot"></span><span class="dot"></span></span>
                <p v-if="message.error" class="message-error" role="alert">本次处理失败：{{ message.error }}</p>
                <TaskProgress v-if="message.status" :done="message.done" :running="message.running" :status="message.status" />
              </template>
            </div>
            <div v-if="message.role === 'assistant' && !message.pending" class="message-tools">
              <button v-if="message.text" class="icon-button" :aria-label="copiedMessage === message.id ? '已复制回答' : '复制回答'" :title="copiedMessage === message.id ? '已复制' : '复制回答'" @click="copyMessage(message)"><AppIcon :name="copiedMessage === message.id ? 'check' : 'copy'" :size="17" /></button>
              <span class="meta">{{ formatTime(message.timestamp) }}</span>
            </div>
          </div>
        </article>
      </div>
    </div>
    <div class="composer-dock">
      <form class="composer" @submit.prevent="send">
        <textarea ref="inputElement" v-model="query" aria-label="问题" placeholder="向掌柜智库提问" rows="1" @keydown="handleKeydown"></textarea>
        <div class="composer-tools">
          <RouterLink class="icon-button attach-button" to="/import" aria-label="导入知识库文件" title="导入知识库文件"><AppIcon name="plus" :size="23" /></RouterLink>
          <label class="stream-toggle"><input v-model="isStream" type="checkbox" :disabled="sending" /><AppIcon name="sparkle" :size="15" /><span>流式回答</span></label>
          <span class="composer-context">知识库 · 联网查询</span>
          <button class="send" type="submit" :disabled="sending || loadingHistory || clearing || !query.trim()" :aria-label="sending ? '正在回答' : '发送问题'" :title="sending ? '正在回答…' : '发送问题'"><span v-if="sending" class="send-spinner"></span><AppIcon v-else name="arrow" :size="21" /></button>
        </div>
      </form>
      <p class="hint">回答由 AI 生成，请核实重要信息。<span>Enter 发送 · Shift + Enter 换行</span></p>
    </div>
  </main>
</template>
