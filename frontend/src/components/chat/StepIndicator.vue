<template>
  <div class="timeline">
    <div v-for="(step, i) in steps" :key="i" class="tl-item" :class="step.status">
      <div class="tl-indicator">
        <span v-if="step.status === 'success'" class="tl-icon success">OK</span>
        <span v-else-if="step.status === 'error'" class="tl-icon error">ER</span>
        <span v-else-if="step.status === 'running'" class="tl-icon running"></span>
        <span v-else class="tl-icon pending"></span>
        <div v-if="i < steps.length - 1" class="tl-line" :class="step.status"></div>
      </div>
      <div class="tl-content">
        <div class="tl-label">{{ step.text }}</div>
        <div v-if="step.time" class="tl-time">{{ formatTime(step.time) }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({ steps: { type: Array, default: () => [] } })

function formatTime(ms) {
  if (!ms) return ''
  if (ms < 1000) return ms + 'ms'
  return (ms / 1000).toFixed(1) + 's'
}
</script>

<style scoped>
.timeline { padding: 8px 0; }
.tl-item { display: flex; gap: 12px; min-height: 36px; }
.tl-indicator { position: relative; display: flex; flex-direction: column; align-items: center; width: 24px; }
.tl-icon { width: 20px; height: 20px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 9px; font-weight: 700; }
.tl-icon.pending { border: 2px solid var(--color-border); background: transparent; }
.tl-icon.running { border: 2px solid var(--color-primary); background: transparent; animation: pulse 1s infinite; }
.tl-icon.success { background: var(--color-success); color: #fff; }
.tl-icon.error { background: var(--color-danger); color: #fff; }
.tl-line { width: 2px; flex: 1; min-height: 12px; margin-top: 2px; }
.tl-line.pending { background: var(--color-border); }
.tl-line.running { background: var(--color-primary); opacity: 0.3; }
.tl-line.success { background: var(--color-success); opacity: 0.3; }
.tl-line.error { background: var(--color-danger); opacity: 0.3; }
.tl-content { padding-bottom: 4px; }
.tl-label { font-size: 13px; color: var(--color-text); line-height: 20px; }
.tl-item.running .tl-label { color: var(--color-primary); font-weight: 500; }
.tl-item.error .tl-label { color: var(--color-danger); }
.tl-time { font-size: 11px; color: var(--color-text-secondary); font-variant-numeric: tabular-nums; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
</style>
