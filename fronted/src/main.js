import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import ChatView from './views/ChatView.vue'
import ImportView from './views/ImportView.vue'
import AuthView from './views/AuthView.vue'
import { checkSession, clearAuthentication, currentUser } from './state/auth.js'
import { safeDestination } from './utils/authRedirect.js'
import './style.css'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/chat', component: ChatView },
    { path: '/import', component: ImportView },
    { path: '/login', component: AuthView, props: { mode: 'login' }, meta: { public: true } },
    { path: '/register', component: AuthView, props: { mode: 'register' }, meta: { public: true } },
    { path: '/:pathMatch(.*)*', redirect: '/chat' },
  ],
})

router.beforeEach(async (to) => {
  const user = await checkSession()
  if (to.meta.public) return user ? safeDestination(to.query.redirect) : true
  return user ? true : { path: '/login', query: { redirect: safeDestination(to.fullPath) } }
})

window.addEventListener('kb:unauthorized', () => {
  clearAuthentication()
  if (!router.currentRoute.value.meta.public) router.replace({ path: '/login', query: { redirect: safeDestination(router.currentRoute.value.fullPath) } })
})

async function recheckAuthentication() {
  if (document.visibilityState !== 'visible' || !currentUser.value) return
  await checkSession()
  if (!currentUser.value && !router.currentRoute.value.meta.public) router.replace('/login')
}
document.addEventListener('visibilitychange', recheckAuthentication)
window.addEventListener('storage', (event) => { if (event.key === 'kb_auth_changed') recheckAuthentication() })

createApp(App).use(router).mount('#app')
