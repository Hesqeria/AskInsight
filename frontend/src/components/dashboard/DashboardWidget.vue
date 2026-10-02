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
    <drill-breadcrumb
      v-if="drillPath.length"
      :path="drillPath"
      @navigate="(idx) => emit('drill-navigate', { widget, idx })"
    />
    <div class="widget-body">
      <chart-renderer
        :type="config.type"
        :category-key="config.categoryKey"
        :value-keys="config.valueKeys"
        :rows="displayRows"
        :sql="widget.sql"
        @drill-down="onDrillDown"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ChartRenderer from '../chart/ChartRenderer.vue'
import DrillBreadcrumb from './DrillBreadcrumb.vue'
import { useDashboardFilters } from '../../composables/useDashboardFilters.js'

const props = defineProps({
  widget: { type: Object, required: true },
  // Fallback rows (already globally filtered) used when the widget has
  // no rows of its own.
  rows: { type: Array, default: () => [] },
  // Global dashboard filters that must also apply to the widget's own
  // rows. Previously this prop existed but was ignored whenever
  // `widget.rows` was set, so the FilterBar had no effect.
  filters: { type: Array, default: () => [] },
  // Per-widget drill-down path (array of { field, value }).
  drillPath: { type: Array, default: () => [] },
})
const emit = defineEmits(['config', 'remove', 'drill-down', 'drill-navigate'])

const config = computed(() => props.widget.config || {})
const { applyFilters } = useDashboardFilters()

// Source rows: prefer the widget's own rows; fall back to the parent-
// supplied (globally filtered) rows. Either way we then layer the
// dashboard filters on top so the FilterBar affects every widget.
const sourceRows = computed(() => {
  const own = props.widget.rows
  return Array.isArray(own) && own.length ? own : props.rows
})
const displayRows = computed(() => applyFilters(sourceRows.value, props.filters))

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
