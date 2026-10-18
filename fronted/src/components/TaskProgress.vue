<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  done: { type: Array, default: () => [] },
  running: { type: Array, default: () => [] },
  status: { type: String, default: 'pending' },
})
const expanded = ref(true)
const statusText = computed(() => ({ pending: '等待中', processing: '处理中', completed: '已完成', failed: '失败' })[props.status] || props.status || '等待中')
watch(() => props.status, (status) => {
  if (['completed', 'failed'].includes(status)) expanded.value = false
})
</script>

<template>
  <details class="progress" :open="expanded" @toggle="expanded = $event.target.open">
    <summary>阶段进度（已完成{{ done.length }}，进行中{{ running.length }}，状态：{{ statusText }}）</summary>
    <ul>
      <li v-for="(name, index) in done" :key="`done-${index}`">✅ {{ name }}</li>
      <li v-for="(name, index) in running" :key="`running-${index}`">⏳ {{ name }}</li>
      <li v-if="!done.length && !running.length">暂无进度</li>
    </ul>
  </details>
</template>
