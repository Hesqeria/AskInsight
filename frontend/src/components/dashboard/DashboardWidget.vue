<template>
  <div class="dashboard-widget">
    <div class="widget-header">
      <span class="widget-title">{{ config.title || '未命名图表' }}</span>
      <div class="widget-actions">
        <button class="icon-btn" @click="emit('config', widget)">
          ⚙
        </button>
        <button class="icon-btn" @click="emit('remove', widget)">
          ×
        </button>
      </div>
    </div>
    <div class="widget-body">
      <chart-renderer
        :type="config.type"
        :category-key="config.categoryKey"
        :value-keys="config.valueKeys"
        :rows="widget.rows || rows"
        :sql="widget.sql"
        @drill-down="onDrillDown"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ChartRenderer from '../chart/ChartRenderer.vue'

const props = defineProps({
  widget: { type: Object, required: true },
  rows: { type: Array, default: () => [] },
})
const emit = defineEmits(['config', 'remove', 'drill-down'])

const config = computed(() => props.widget.config || {})

function onDrillDown(event) {
  emit('drill-down', { widget: props.widget, ...event })
}
</script>

<style scoped>
.dashboard-widget { width: 100%; height: 100%; display: flex; flex-direction: column; background: var(--color-bg); border: 1px solid var(--color-border); border-radius: 8px; overflow: hidden; }
.widget-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; border-bottom: 1px solid var(--color-border); }
.widget-title { font-weight: 600; font-size: 14px; }
.widget-actions { display: flex; gap: 4px; }
.icon-btn { background: none; border: none; cursor: pointer; color: var(--color-text-muted); font-size: 14px; padding: 2px 6px; }
.icon-btn:hover { color: var(--color-primary); }
.widget-body { flex: 1; min-height: 0; padding: 8px; }
</style>
