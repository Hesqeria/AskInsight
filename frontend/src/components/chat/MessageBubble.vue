<template>
  <div v-if="msg.type === 'text'" class="bubble-text">{{ msg.content }}</div>

  <div v-else-if="msg.type === 'steps'" class="steps">
    <div v-for="(step, i) in msg.steps" :key="i" class="step-row" :class="step.status">
      <span class="step-dot" :class="step.status"></span>
      <span class="step-text">{{ step.text }}</span>
      <span v-if="step.time" class="step-time">{{ formatTime(step.time) }}</span>
    </div>
  </div>

  <div v-else-if="msg.type === 'card'" class="card-result">
    <div class="card-value">{{ msg.value }}</div>
    <div class="card-label">{{ msg.label }}</div>
  </div>

  <div v-else-if="msg.type === 'error'" class="error-text">Error: {{ msg.content }}</div>
  <div v-else-if="msg.type === 'reply'" class="bubble-text">{{ msg.content }}</div>
</template>

<script setup>
defineProps({ msg: { type: Object, required: true } })

function formatTime(ms) {
  if (ms < 1000) return ms + 'ms'
  return (ms / 1000).toFixed(1) + 's'
}
</script>

<style scoped>
.steps { display: flex; flex-direction: column; gap: 6px; padding: 4px 0; }
.step-row { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--color-text-secondary); }
.step-row.running { color: var(--color-primary); }
.step-row.success { color: var(--color-success); }
.step-row.error { color: var(--color-danger); }
.step-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.step-dot.running { background: var(--color-primary); animation: pulse 1s infinite; }
.step-dot.success { background: var(--color-success); }
.step-dot.error { background: var(--color-danger); }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
.step-text { flex: 1; }
.step-time { font-size: 11px; opacity: 0.7; font-variant-numeric: tabular-nums; }
.card-result { text-align: center; padding: 16px; }
.card-value { font-size: 32px; font-weight: 700; color: var(--color-primary); font-variant-numeric: tabular-nums; }
.card-label { font-size: 13px; color: var(--color-text-secondary); margin-top: 4px; }
.error-text { color: var(--color-danger); font-weight: 500; }
</style>
