// 浏览器负责 SSE 解码；这里统一处理终止、异常和资源释放。
export function connectQueryStream(url, handlers, EventSourceClass = globalThis.EventSource) {
  const source = new EventSourceClass(url)
  let closed = false
  const close = () => {
    if (closed) return
    closed = true
    source.close()
  }
  const fail = (error) => {
    if (closed) return
    close()
    handlers.onError(error instanceof Error ? error : new Error(String(error)))
  }
  const listen = (name, handler) => {
    source.addEventListener(name, (event) => {
      if (closed) return
      try {
        handler(JSON.parse(event.data || '{}'))
      } catch (error) {
        fail(new Error(`流式数据处理失败：${error.message}`))
      }
    })
  }
  listen('progress', (data) => {
    handlers.onProgress(data)
    if (data.status === 'failed') fail(new Error(data.error || '问答处理失败'))
  })
  listen('delta', (data) => handlers.onDelta(data.delta || ''))
  const finish = (data) => {
    // 等待 final，不能在 completed 进度事件中提前释放发送状态。
    handlers.onFinal(data)
    close()
  }
  listen('final', finish)
  listen('final_answer', finish)
  source.addEventListener('error', (event) => {
    if (closed) return
    let message = 'SSE 连接中断或失败，请重试'
    try {
      if (event.data) message = JSON.parse(event.data).error || message
    } catch {
      message = '服务端返回了无法解析的错误信息'
    }
    fail(new Error(message))
  })
  return close
}
