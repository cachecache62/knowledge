<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from './components/AppIcon.vue'
import { activeSessionId, conversationBusy, conversations, newConversation, selectConversation } from './state/conversations.js'
import { authChecked, currentUser, signOut } from './state/auth.js'
import { chatToolbar } from './state/chatToolbar.js'

const route = useRoute()
const router = useRouter()
const sidebarOpen = ref(false)
const sidebarCollapsed = ref(false)
const searchOpen = ref(false)
const search = ref('')
const searchInput = ref(null)
const loggingOut = ref(false)
const logoutError = ref('')
const filteredConversations = computed(() => conversations.value.filter(item => item.title.toLowerCase().includes(search.value.trim().toLowerCase())))
const pageTitle = computed(() => ({ '/import': '资料库', '/login': '账号中心', '/register': '账号中心' })[route.path] || '掌柜智库')

function startChat() {
  newConversation()
  router.push('/chat')
  sidebarOpen.value = false
}

function openConversation(id) {
  selectConversation(id)
  router.push('/chat')
  sidebarOpen.value = false
}

async function openSearch() {
  searchOpen.value = !searchOpen.value
  if (!searchOpen.value) search.value = ''
  await nextTick()
  searchInput.value?.focus()
}

watch(() => route.path, () => { sidebarOpen.value = false })

async function logout() {
  if (loggingOut.value) return
  loggingOut.value = true
  logoutError.value = ''
  try {
    await signOut()
    await router.replace('/login')
  } catch { logoutError.value = '退出失败，请检查账户服务连接后重试。' }
  finally { loggingOut.value = false }
}
</script>

<template>
  <div v-if="!authChecked" class="auth-loading" role="status"><span class="send-spinner"></span><span>正在验证登录状态…</span></div>
  <div v-else-if="route.meta.public || !currentUser" class="auth-shell">
    <header class="auth-shell-header"><span class="site-name">掌柜智库<span class="brand-dot"></span></span><span>你的知识，触手可及</span></header>
    <RouterView v-if="route.meta.public" />
    <div v-else class="auth-loading" role="status">正在前往登录页…</div>
  </div>
  <div v-else class="workspace" :key="currentUser.id" :class="{ 'sidebar-collapsed': sidebarCollapsed, 'sidebar-open': sidebarOpen }" @keydown.esc="sidebarOpen = false">
    <button v-if="sidebarOpen" class="sidebar-backdrop" aria-label="关闭导航" @click="sidebarOpen = false"></button>
    <aside class="sidebar" aria-label="工作空间导航">
      <div class="sidebar-heading">
        <RouterLink class="site-name" to="/chat">掌柜智库<span class="brand-dot"></span></RouterLink>
        <button class="icon-button desktop-collapse" title="收起侧边栏" aria-label="收起侧边栏" @click="sidebarCollapsed = true"><AppIcon name="panel" :size="19" /></button>
        <button class="icon-button mobile-close" aria-label="关闭导航" @click="sidebarOpen = false"><AppIcon name="close" /></button>
      </div>

      <nav class="primary-nav" aria-label="页面导航">
        <button class="nav-item" :disabled="conversationBusy" @click="startChat"><AppIcon name="edit" /><span>新对话</span><span class="nav-plus">+</span></button>
        <button class="nav-item" :class="{ selected: searchOpen }" :aria-expanded="searchOpen" @click="openSearch"><AppIcon name="search" /><span>搜索对话</span></button>
        <RouterLink class="nav-item" active-class="selected" to="/import"><AppIcon name="library" /><span>资料库</span><span class="nav-badge">PDF · MD</span></RouterLink>
      </nav>

      <div class="sidebar-history">
        <div class="sidebar-label">最近对话</div>
        <div v-if="searchOpen" class="history-search"><AppIcon name="search" :size="15" /><input ref="searchInput" v-model="search" aria-label="搜索最近对话" placeholder="搜索最近对话…" /><button v-if="search" class="icon-button" aria-label="清空搜索" @click="search = ''"><AppIcon name="close" :size="14" /></button></div>
        <div class="conversation-list">
          <button v-for="item in filteredConversations" :key="item.id" class="conversation-item" :class="{ active: route.path === '/chat' && item.id === activeSessionId }" :disabled="conversationBusy" :title="item.title" :aria-current="route.path === '/chat' && item.id === activeSessionId ? 'page' : undefined" @click="openConversation(item.id)"><span>{{ item.title }}</span></button>
          <p v-if="!filteredConversations.length" class="history-empty">{{ search ? '没有找到相关对话' : '从一个问题开始，\n让知识在这里积累。' }}</p>
        </div>
      </div>

      <div class="sidebar-footer">
        <div class="workspace-note"><span class="workspace-note-dot"></span>你的知识，触手可及</div>
        <div class="account-link"><span class="account-avatar">{{ currentUser.email[0].toUpperCase() }}</span><span class="account-copy"><strong>我的工作空间</strong><small :title="currentUser.email">{{ currentUser.email }}</small></span><button class="icon-button" :disabled="loggingOut" aria-label="退出登录" title="退出登录" @click="logout"><AppIcon name="logout" :size="18" /></button></div>
        <p v-if="logoutError" class="logout-error" role="alert">{{ logoutError }}</p>
      </div>
    </aside>

    <div class="workspace-main">
      <header class="shell-header">
        <div class="shell-heading">
          <button class="icon-button sidebar-toggle" :class="{ 'show-desktop': sidebarCollapsed }" aria-label="展开导航" :aria-expanded="sidebarOpen || !sidebarCollapsed" @click="sidebarCollapsed ? sidebarCollapsed = false : sidebarOpen = !sidebarOpen"><AppIcon name="panel" /></button>
          <span class="page-title">{{ pageTitle }}</span>
          <span v-if="route.path === '/chat'" class="page-subtitle">知识库问答</span>
        </div>
        <div id="page-actions" class="page-actions">
          <template v-if="route.path === '/chat' && chatToolbar">
            <span class="connection-status" :class="{ connected: chatToolbar.connected }" role="status"><span></span>{{ chatToolbar.connected ? '服务已连接' : '服务未连接' }}</span>
            <button class="text-button clear-button" :disabled="chatToolbar.disabled" @click="chatToolbar.clear"><AppIcon name="trash" :size="16" /><span>{{ chatToolbar.clearing ? '清空中…' : '清空对话' }}</span></button>
          </template>
        </div>
      </header>
      <RouterView v-slot="{ Component }">
        <KeepAlive :key="currentUser.id" exclude="AuthView"><component :is="Component" /></KeepAlive>
      </RouterView>
    </div>
  </div>
</template>
