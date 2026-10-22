import { shallowRef } from 'vue'

// 工具栏由外层布局渲染，避免跨账号销毁布局时 Teleport 仍引用旧 DOM。
export const chatToolbar = shallowRef(null)
