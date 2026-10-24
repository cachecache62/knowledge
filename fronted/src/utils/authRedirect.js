// 只允许跳回应用内已有的业务页面，拒绝外部网址和账号页重定向循环。
export function safeDestination(value) {
  return typeof value === 'string' && /^\/(chat|import|documents|trash)(?:[?#]|$)/.test(value) ? value : '/chat'
}
