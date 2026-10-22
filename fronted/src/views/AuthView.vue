<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authenticate, authError } from '../state/auth.js'
import { safeDestination } from '../utils/authRedirect.js'

defineOptions({ name: 'AuthView' })
const props = defineProps({ mode: { type: String, default: 'login' } })
const isRegister = computed(() => props.mode === 'register')
const form = reactive({ email: '', password: '', confirmPassword: '' })
const errors = reactive({ email: '', password: '', confirmPassword: '' })
const emailInput = ref(null)
const passwordInput = ref(null)
const confirmInput = ref(null)
const showPassword = ref(false)
const showConfirm = ref(false)
const submitted = ref(false)
const feedback = ref('')
const submitting = ref(false)
const route = useRoute()
const router = useRouter()
let submitController
let submitVersion = 0
const accountLinkQuery = computed(() => route.query.redirect ? { redirect: safeDestination(route.query.redirect) } : {})

function validate(field) {
  if (field === 'email') {
    errors.email = !form.email.trim()
      ? '请输入邮箱地址'
      : emailInput.value?.validity.typeMismatch ? '请输入有效的邮箱地址，例如 name@example.com' : ''
  } else if (field === 'password') {
    errors.password = !form.password
      ? '请输入密码'
      : isRegister.value && form.password.length < 8 ? '密码至少需要 8 位' : form.password.length > 128 ? '密码不能超过 128 位' : ''
  } else if (field === 'confirmPassword') {
    errors.confirmPassword = !form.confirmPassword
      ? '请再次输入密码'
      : form.confirmPassword !== form.password ? '两次输入的密码不一致' : ''
  }
  return !errors[field]
}

function onInput(field) {
  feedback.value = ''
  if (submitted.value || errors[field]) validate(field)
  if (field === 'password' && isRegister.value && form.confirmPassword) validate('confirmPassword')
}

function onBlur(field) {
  if (form[field] || submitted.value) validate(field)
}

async function submit() {
  if (submitting.value) return
  submitted.value = true
  form.email = form.email.trim()
  await nextTick()
  const fields = isRegister.value ? ['email', 'password', 'confirmPassword'] : ['email', 'password']
  const invalid = fields.filter(field => !validate(field))
  if (invalid.length) {
    feedback.value = '请检查标记的字段后再提交。'
    const inputs = { email: emailInput, password: passwordInput, confirmPassword: confirmInput }
    inputs[invalid[0]].value?.focus()
    return
  }
  submitting.value = true
  feedback.value = ''
  const version = ++submitVersion
  submitController = new AbortController()
  try {
    await authenticate(isRegister.value ? 'register' : 'login', { email: form.email, password: form.password }, submitController.signal)
    if (version !== submitVersion || submitController.signal.aborted) return
    form.password = ''
    form.confirmPassword = ''
    await router.replace(safeDestination(route.query.redirect))
  } catch (error) {
    if (version !== submitVersion || submitController.signal.aborted) return
    feedback.value = error.message || '账户服务请求失败，请稍后重试。'
    if (error.status === 409) errors.email = error.message
  } finally {
    if (version === submitVersion) submitting.value = false
  }
}

watch(() => props.mode, () => {
  submitVersion++
  submitController?.abort()
  submitting.value = false
  form.password = ''
  form.confirmPassword = ''
  Object.keys(errors).forEach(field => { errors[field] = '' })
  showPassword.value = false
  showConfirm.value = false
  submitted.value = false
  feedback.value = ''
})
onBeforeUnmount(() => { submitVersion++; submitController?.abort(); form.password = ''; form.confirmPassword = '' })
</script>

<template>
  <main class="auth-app" aria-labelledby="auth-heading">
    <header class="topbar">
      <div class="brand">
        <div class="logo" aria-hidden="true"></div>
        <div class="title">
          <strong>掌柜智库 · 账号中心</strong>
          <span>连接资料、知识与每一个好问题</span>
        </div>
      </div>
      <div class="top-actions"><span class="pill">账号中心</span></div>
    </header>

    <div class="auth-content">
      <aside class="auth-intro" aria-label="掌柜智库介绍">
        <span class="auth-eyebrow">KNOWLEDGE WORKSPACE</span>
        <h2>让资料有处安放，<br><span>让知识随时可用。</span></h2>
        <p class="auth-intro-copy">从一份文档到一个答案，<br>在掌柜智库，连接你的资料与想法。</p>

        <div class="auth-illustration" aria-hidden="true">
          <div class="auth-document auth-document-back"></div>
          <div class="auth-document">
            <div class="auth-document-header">
              <span class="auth-document-icon">文</span>
              <div><strong>我的知识资料</strong><span>文档与笔记 · 示意</span></div>
              <span class="auth-document-dot"></span>
            </div>
            <div class="auth-document-line"></div>
            <div class="auth-document-line short"></div>
            <div class="auth-document-line"></div>
            <div class="auth-document-tags"><span>文档</span><span>知识</span><span>想法</span></div>
          </div>
          <div class="auth-question">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 4h16v12H9l-5 4V4Z" stroke-linejoin="round"/><path d="M8 8h8M8 12h5" stroke-linecap="round"/></svg>
            <span>从知识中，找到下一步的灵感</span>
          </div>
        </div>

        <div class="auth-capabilities">
          <div><span class="auth-step">01</span><span>导入资料<small>将文档整理进知识库</small></span></div>
          <div><span class="auth-step">02</span><span>提出问题<small>围绕知识展开查询</small></span></div>
        </div>
      </aside>

      <section class="auth-form-section">
        <div class="auth-mode-links" aria-label="账号页面">
          <RouterLink :to="{ path: '/login', query: accountLinkQuery }">登录</RouterLink>
          <RouterLink :to="{ path: '/register', query: accountLinkQuery }">注册</RouterLink>
        </div>
        <h1 id="auth-heading">{{ isRegister ? '创建你的账号' : '欢迎回来' }}</h1>
        <p class="auth-subtitle">{{ isRegister ? '从这里开始，建立你的知识空间。' : '登录掌柜智库，继续你的知识探索。' }}</p>

        <form novalidate :aria-busy="submitting" @submit.prevent="submit">
          <div class="auth-field">
            <label for="auth-email">邮箱地址</label>
            <div class="auth-input-wrap">
              <svg class="auth-input-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7" stroke-linejoin="round"/></svg>
              <input id="auth-email" ref="emailInput" v-model="form.email" type="email" name="email" required
                :autocomplete="isRegister ? 'email' : 'username'" autocapitalize="none" :spellcheck="false"
                placeholder="请输入邮箱地址" :aria-invalid="Boolean(errors.email)" aria-describedby="auth-email-error"
                @input="onInput('email')" @blur="onBlur('email')" />
            </div>
            <p v-if="errors.email" id="auth-email-error" class="auth-error">{{ errors.email }}</p>
          </div>

          <div class="auth-field">
            <label for="auth-password">{{ isRegister ? '设置密码' : '密码' }}</label>
            <div class="auth-input-wrap">
              <svg class="auth-input-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" stroke-linecap="round"/></svg>
              <input id="auth-password" ref="passwordInput" v-model="form.password" :type="showPassword ? 'text' : 'password'"
                name="password" required :autocomplete="isRegister ? 'new-password' : 'current-password'"
                :placeholder="isRegister ? '请设置至少 8 位密码' : '请输入密码'" :aria-invalid="Boolean(errors.password)"
                :aria-describedby="isRegister ? 'auth-password-error auth-password-hint' : 'auth-password-error'"
                @input="onInput('password')" @blur="onBlur('password')" />
              <button class="auth-reveal" type="button" :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                :aria-pressed="showPassword" aria-controls="auth-password" @click="showPassword = !showPassword">
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" stroke-linejoin="round"/><circle cx="12" cy="12" r="3"/><path v-if="showPassword" d="m3 3 18 18" stroke-linecap="round"/></svg>
              </button>
            </div>
            <p v-if="errors.password" id="auth-password-error" class="auth-error">{{ errors.password }}</p>
            <p v-if="isRegister" id="auth-password-hint" class="auth-field-hint">至少 8 位，建议使用不易猜测的密码。</p>
          </div>

          <div v-if="isRegister" class="auth-field">
            <label for="auth-confirm">确认密码</label>
            <div class="auth-input-wrap">
              <svg class="auth-input-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" stroke-linecap="round"/></svg>
              <input id="auth-confirm" ref="confirmInput" v-model="form.confirmPassword" :type="showConfirm ? 'text' : 'password'"
                name="confirmPassword" required autocomplete="new-password" placeholder="请再次输入密码"
                :aria-invalid="Boolean(errors.confirmPassword)" aria-describedby="auth-confirm-error"
                @input="onInput('confirmPassword')" @blur="onBlur('confirmPassword')" />
              <button class="auth-reveal" type="button" :aria-label="showConfirm ? '隐藏确认密码' : '显示确认密码'"
                :aria-pressed="showConfirm" aria-controls="auth-confirm" @click="showConfirm = !showConfirm">
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" stroke-linejoin="round"/><circle cx="12" cy="12" r="3"/><path v-if="showConfirm" d="m3 3 18 18" stroke-linecap="round"/></svg>
              </button>
            </div>
            <p v-if="errors.confirmPassword" id="auth-confirm-error" class="auth-error">{{ errors.confirmPassword }}</p>
          </div>

          <button class="btn auth-submit" type="submit" :disabled="submitting">
            {{ submitting ? '正在提交…' : isRegister ? '创建账号' : '登录' }}
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M5 12h14m-5-5 5 5-5 5" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
          <div class="auth-feedback" role="status" aria-live="polite" aria-atomic="true"><p v-if="feedback || authError">{{ feedback || authError }}</p></div>
        </form>

        <p class="auth-switch">
          {{ isRegister ? '已有账号？' : '还没有账号？' }}
          <RouterLink :to="{ path: isRegister ? '/login' : '/register', query: accountLinkQuery }">{{ isRegister ? '去登录' : '立即注册' }}</RouterLink>
        </p>
        <p class="auth-preview-note">{{ isRegister ? '创建账号后自动登录，即可开始使用知识库。' : '登录后即可使用知识库问答与资料导入。' }}</p>
      </section>
    </div>
  </main>
</template>

<style scoped>
.auth-app { flex: 1; min-height: 0; overflow-y: auto; padding: 24px 28px 48px; }
.auth-app > .topbar { display: none; }
.auth-content { display: grid; grid-template-columns: 1fr 1fr; max-width: 900px; min-height: 560px; margin: 18px auto; border: 1px solid #e9e9e9; border-radius: 20px; overflow: hidden; }
.auth-intro { background: #f8f9f6; padding: 52px 32px; border-right: 1px solid #e9e9e9; }
.auth-eyebrow { font-size: 9px; letter-spacing: 2px; color: #919987; }
.auth-intro h2 { font-size: 27px; line-height: 1.7; font-weight: 500; letter-spacing: -1px; margin: 25px 0 18px; }
.auth-intro h2 span { color: #738267; }
.auth-intro-copy { font-size: 12px; color: #92968d; line-height: 2; }
.auth-illustration { position: relative; height: 195px; margin: 34px 0 26px; }
.auth-document { position: absolute; width: 92%; top: 12px; left: 0; padding: 19px; background: #fff; border: 1px solid #e4e7df; border-radius: 12px; }
.auth-document-back { background: #edf0e8; transform: rotate(-5deg); top: 0; left: 8px; height: 145px; }
.auth-document-header { display: flex; align-items: center; gap: 9px; margin-bottom: 17px; }
.auth-document-icon { width: 29px; height: 34px; background: #f1f3ed; color: #89957c; border-radius: 5px; display: grid; place-items: center; font-size: 12px; }
.auth-document-header strong { display: block; font-size: 11px; font-weight: 500; }
.auth-document-header div span { display: block; font-size: 9px; color: #a2a69b; margin-top: 5px; }
.auth-document-dot { width: 5px; height: 5px; background: #a2b293; border-radius: 50%; margin-left: auto; }
.auth-document-line { width: 87%; height: 4px; background: #eef0eb; border-radius: 4px; margin-bottom: 8px; }
.auth-document-line.short { width: 66%; }
.auth-document-tags { display: flex; gap: 6px; margin-top: 15px; }
.auth-document-tags span { font-size: 9px; color: #9ba38f; background: #f4f5f1; padding: 4px 7px; border-radius: 4px; }
.auth-question { position: absolute; bottom: 0; right: 0; display: flex; align-items: center; gap: 8px; padding: 12px 13px; border: 1px solid #e5e8df; border-radius: 9px; background: #fff; font-size: 10px; color: #879579; }
.auth-capabilities { display: flex; gap: 20px; }
.auth-capabilities > div { display: flex; align-items: center; gap: 8px; font-size: 11px; color: #7e8676; }
.auth-step { font-size: 10px; color: #9da78f; }
.auth-capabilities small { display: block; font-size: 9px; color: #a2a698; margin-top: 5px; }
.auth-form-section { padding: 35px 37px; align-self: center; min-width: 0; }
.auth-mode-links { display: flex; gap: 20px; margin-bottom: 26px; border-bottom: 1px solid #ececec; }
.auth-mode-links a { font-size: 13px; padding: 0 2px 11px; text-decoration: none; color: #aaa; }
.auth-mode-links a.router-link-active { color: #333; border-bottom: 2px solid #333; margin-bottom: -1px; }
.auth-form-section h1 { font-size: 25px; font-weight: 500; margin: 0 0 10px; text-align: left; }
.auth-subtitle { font-size: 12px; color: #999; line-height: 1.8; margin: 0 0 25px; }
.auth-field { margin-bottom: 17px; }
.auth-field label { display: block; font-size: 12px; color: #777; margin-bottom: 8px; }
.auth-input-wrap { position: relative; }
.auth-input-icon { position: absolute; left: 12px; top: 14px; color: #aaa; pointer-events: none; }
.auth-field input { width: 100%; height: 46px; border: 1px solid #e0e0e0; border-radius: 10px; padding: 0 43px 0 39px; font-size: 13px; color: #333; background: #fff; outline: none; }
.auth-field input[type=email] { padding-right: 12px; }
.auth-field input::placeholder { color: #bbb; }
.auth-field input:focus { border-color: #999; }
.auth-field input[aria-invalid=true] { border-color: #bd8181; }
.auth-reveal { position: absolute; right: 3px; top: 3px; border: 0; border-radius: 8px; width: 40px; height: 40px; display: grid; place-items: center; background: transparent; color: #aaa; }
.auth-reveal:hover { background: #f4f4f4; }
.auth-error { font-size: 11px; color: #b87070; margin: 6px 0 0; }
.auth-field-hint { font-size: 10px; color: #aaa; margin: 7px 0 0; line-height: 1.7; }
.auth-submit { display: flex; align-items: center; justify-content: center; gap: 10px; width: 100%; height: 44px; margin-top: 8px; border-radius: 22px; font-size: 13px; }
.auth-feedback p { font-size: 11px; line-height: 1.8; color: #8a826c; background: #f7f5ee; border-radius: 8px; padding: 10px; margin: 15px 0 0; }
.auth-switch { font-size: 11px; color: #aaa; text-align: center; margin: 23px 0 0; }
.auth-switch a { color: #666; text-underline-offset: 3px; margin-left: 5px; }
.auth-preview-note { font-size: 10px; color: #aaa; text-align: center; line-height: 1.8; margin: 18px 0 0; }
@media (max-width: 1050px) { .auth-content { grid-template-columns: 1fr; max-width: 440px; min-height: auto; } .auth-intro { display: none; } }
@media (max-width: 760px) { .auth-app { padding: 12px 20px 30px; } .auth-content { border: 0; margin: 0 auto; } .auth-form-section { padding: 24px 10px; } }
</style>
