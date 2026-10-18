<script setup>
import { computed } from 'vue'
import { inlineTokens } from '../utils/markdown.js'
const props = defineProps({ text: { type: String, default: '' } })
const tokens = computed(() => inlineTokens(props.text))
</script>

<template>
  <template v-for="(token, index) in tokens" :key="index"><code v-if="token.type === 'code'" class="inline-code">{{ token.text }}</code><strong v-else-if="token.type === 'strong'">{{ token.text }}</strong><a v-else-if="token.type === 'link'" :href="token.href" target="_blank" rel="noopener noreferrer">{{ token.text }}</a><template v-else>{{ token.text }}</template></template>
</template>
