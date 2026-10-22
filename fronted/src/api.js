export const IMPORT_API_BASE = (import.meta.env.VITE_IMPORT_API_BASE || 'http://127.0.0.1:8000').replace(/\/+$/, '')
export const QUERY_API_BASE = (import.meta.env.VITE_QUERY_API_BASE || 'http://127.0.0.1:8001').replace(/\/+$/, '')
export const AUTH_API_BASE = (import.meta.env.VITE_AUTH_API_BASE || 'http://127.0.0.1:8002').replace(/\/+$/, '')

export async function requestJson(base, path, options = {}) {
  const response = await fetch(`${base}${path}`, { ...options, credentials: 'include' })
  if (response.status === 401 && !path.startsWith('/auth/')) {
    window.dispatchEvent(new Event('kb:unauthorized'))
  }
  const text = await response.text()
  let data
  try {
    data = text ? JSON.parse(text) : {}
  } catch {
    throw new Error(response.ok ? '服务端返回了无法解析的数据' : `请求失败（HTTP ${response.status}）`)
  }
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data.message || '')
    const error = new Error(detail || `请求失败（HTTP ${response.status}）`)
    error.status = response.status
    throw error
  }
  return data
}
