<template>
  <div class="input-wrapper">
    <div v-if="suggestions.length" class="suggestion-bar">
      <span v-for="(s, i) in suggestions" :key="i" class="suggestion-tag" @click="emit('quickAsk', s)">{{ s }}</span>
    </div>
    <div class="input-box">
      <textarea
        ref="inputEl"
        v-model="localQuestion"
        :placeholder="placeholder"
        :disabled="loading"
        rows="1"
        @keydown="onKeyDown"
        @input="onInput"
      ></textarea>
      <button class="send-btn" :disabled="loading || !localQuestion.trim()" @click="send">
        <span v-if="loading">...</span>
        <span v-else>&#8593;</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  loading: { type: Boolean, default: false },
  placeholder: { type: String, default: 'Ask your data question...' },
  suggestions: { type: Array, default: () => [] },
})

const emit = defineEmits(['send', 'quickAsk'])
const localQuestion = ref('')
const inputEl = ref(null)

function send() {
  if (!localQuestion.value.trim() || props.loading) return
  emit('send', localQuestion.value.trim())
  localQuestion.value = ''
}

function onKeyDown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

function onInput() {
  const el = inputEl.value
  if (el) {
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }
}
</script>

<style scoped>
.input-wrapper { padding-top: 8px; }
.suggestion-bar { display: flex; gap: 6px; flex-wrap: wrap; justify-content: center; margin-bottom: 10px; }
.suggestion-tag { padding: 4px 12px; border-radius: var(--radius-round); background: var(--color-bg-tertiary); color: var(--color-text-secondary); font-size: var(--font-size-sm); cursor: pointer; transition: all var(--transition); }
.suggestion-tag:hover { background: rgba(64,158,255,0.1); color: var(--color-primary); }
.input-box { display: flex; gap: 10px; align-items: flex-end; padding: 10px 14px; border-radius: var(--radius-lg); background: var(--color-bg); border: 1px solid var(--color-border); transition: border-color var(--transition); box-shadow: var(--shadow-sm); }
.input-box:focus-within { border-color: var(--color-primary); box-shadow: var(--shadow); }
textarea { flex: 1; border: none; outline: none; background: transparent; font-size: var(--font-size-base); color: var(--color-text); resize: none; min-height: 24px; line-height: 1.5; font-family: inherit; }
textarea::placeholder { color: var(--color-text-muted); }
textarea:disabled { opacity: 0.5; }
.send-btn { width: 34px; height: 34px; border-radius: 50%; border: none; background: var(--color-primary); color: #fff; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all var(--transition); flex-shrink: 0; font-size: 18px; }
.send-btn:disabled { opacity: 0.4; cursor: not-allowed; background: var(--color-text-muted); }
.send-btn:not(:disabled):hover { background: var(--color-primary-dark); }
</style>
