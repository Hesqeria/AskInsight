<template>
  <div class="chart-actions">
    <button class="action-btn" title="下载 PNG" @click="emit('download-png')">
      PNG
    </button>
    <button class="action-btn" title="下载 CSV" @click="emit('download-csv')">
      CSV
    </button>
    <button v-if="sql" class="action-btn" title="复制 SQL" @click="emit('copy-sql')">
      SQL
    </button>
  </div>
</template>

<script setup>
defineProps({
  sql: { type: String, default: '' },
})
const emit = defineEmits(['download-png', 'download-csv', 'copy-sql'])
</script>

<style scoped>
.chart-actions { position: absolute; top: 4px; left: 8px; display: flex; gap: 4px; opacity: 0; transition: opacity 0.2s; pointer-events: none; }
.action-btn { padding: 2px 6px; font-size: 10px; border: 1px solid var(--color-border); background: var(--color-bg); border-radius: 4px; cursor: pointer; color: var(--color-text); }
.action-btn:hover { background: var(--color-primary); color: #fff; border-color: var(--color-primary); }
</style>
