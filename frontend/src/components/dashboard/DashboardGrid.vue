<template>
  <div class="dashboard-grid">
    <grid-layout
      :layout="layoutModel"
      :col-num="12"
      :row-height="60"
      :is-draggable="true"
      :is-resizable="true"
      :vertical-compact="true"
      :use-css-transforms="true"
      @layout-updated="onLayoutUpdated"
    >
      <grid-item
        v-for="item in layoutModel"
        :key="item.i"
        :x="item.x"
        :y="item.y"
        :w="item.w"
        :h="item.h"
        :i="item.i"
        drag-allow-from=".widget-header"
      >
        <dashboard-widget
          :widget="findWidget(item.i)"
          :rows="rows"
          :filters="filters"
          :drill-path="drillPaths[String(item.i)] || []"
          @config="$emit('config', $event)"
          @remove="$emit('remove', $event)"
          @drill-down="$emit('drill-down', $event)"
          @drill-navigate="$emit('drill-navigate', $event)"
        />
      </grid-item>
    </grid-layout>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { GridLayout, GridItem } from 'vue-grid-layout-v3'
import DashboardWidget from './DashboardWidget.vue'

const props = defineProps({
  widgets: { type: Array, default: () => [] },
  rows: { type: Array, default: () => [] },
  filters: { type: Array, default: () => [] },
  drillPaths: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:layout', 'config', 'remove', 'drill-down', 'drill-navigate'])

const layoutModel = computed(() =>
  props.widgets.map(w => ({ i: String(w.id), x: w.x, y: w.y, w: w.w, h: w.h }))
)

function findWidget(id) {
  return props.widgets.find(w => String(w.id) === String(id)) || {}
}

function onLayoutUpdated(newLayout) {
  // Emit position deltas only; parent applies without re-deriving the whole widget set.
  const patch = {}
  for (const item of newLayout) {
    patch[item.i] = { x: item.x, y: item.y, w: item.w, h: item.h }
  }
  emit('update:layout', patch)
}
</script>

<style scoped>
.dashboard-grid { width: 100%; min-height: 400px; }
</style>
