<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { parseAnswer } from '../utils/answer.js'
import { markdownBlocks } from '../utils/markdown.js'
import MarkdownInline from './MarkdownInline.vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  text: { type: String, default: '' },
  imageUrls: { type: Array, default: () => [] },
  pending: { type: Boolean, default: false },
})
const answer = computed(() => parseAnswer(props.text, props.imageUrls))
const blocks = computed(() => markdownBlocks(answer.value.text.replace(/!\[[^\]]*\]\((https?:\/\/[^\n]+?)\)/g, (match, url) => answer.value.images.includes(url) ? '' : match)))
const failedImages = ref(new Set())
const copied = ref(-1)
const copyError = ref('')
let copyTimer
async function copyCode(text, index) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = index
    copyError.value = ''
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copied.value = -1 }, 2000)
  } catch { copyError.value = '复制失败，请选择代码后手动复制。' }
}
watch(() => props.imageUrls, () => { failedImages.value = new Set() })
onBeforeUnmount(() => clearTimeout(copyTimer))
</script>

<template>
  <div class="answer">
    <div class="markdown-content">
      <template v-for="(block, index) in blocks" :key="index">
        <div v-if="block.type === 'code'" class="code-block">
          <div class="code-heading"><span><span class="code-symbol">&lt;/&gt;</span>{{ block.language }}</span><button class="icon-button" :aria-label="copied === index ? '代码已复制' : '复制代码'" :title="copied === index ? '已复制' : '复制代码'" @click="copyCode(block.text, index)"><AppIcon :name="copied === index ? 'check' : 'copy'" :size="17" /></button></div>
          <pre><code>{{ block.text }}</code></pre>
        </div>
        <component :is="`h${block.level}`" v-else-if="block.type === 'heading'"><MarkdownInline :text="block.text" /></component>
        <component :is="block.ordered ? 'ol' : 'ul'" v-else-if="block.type === 'list'" :start="block.start"><li v-for="(item, itemIndex) in block.items" :key="itemIndex"><MarkdownInline :text="item" /></li></component>
        <blockquote v-else-if="block.type === 'quote'"><MarkdownInline :text="block.text" /></blockquote>
        <hr v-else-if="block.type === 'rule'" />
        <p v-else><MarkdownInline :text="block.text" /></p>
      </template>
      <p v-if="!answer.text && !pending">（已完成，但未返回答案）</p>
      <p v-if="copyError" class="message-error" role="status">{{ copyError }}</p>
    </div>
    <div v-if="answer.images.length" class="answer-images">
      <div v-for="url in answer.images" :key="url">
        <img v-if="!failedImages.has(url)" :src="url" alt="参考图片" loading="lazy" referrerpolicy="no-referrer" @error="failedImages.add(url)" />
        <a :href="url" target="_blank" rel="noopener noreferrer">{{ url }}</a>
      </div>
    </div>
  </div>
</template>
