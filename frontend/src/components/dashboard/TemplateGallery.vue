<template>
  <div class="template-gallery">
    <div v-for="tpl in templates" :key="tpl.key" class="template-card" @click="emit('select', tpl.key)">
      <div class="template-name">{{ tpl.name }}</div>
      <div class="template-count">{{ tpl.widgets.length }} 个图表</div>
    </div>
  </div>
</template>

<script setup>
import { useDashboardTemplates } from '../../composables/useDashboardTemplates.js'

const { templates } = useDashboardTemplates()
const emit = defineEmits(['select'])
</script>

<style scoped>
.template-gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; padding: 12px 0; }
.template-card { border: 1px solid var(--color-border); border-radius: 8px; padding: 16px; cursor: pointer; transition: box-shadow 0.2s; background: var(--color-bg); }
.template-card:hover { box-shadow: var(--shadow); border-color: var(--color-primary); }
.template-name { font-weight: 600; font-size: 14px; margin-bottom: 6px; }
.template-count { font-size: 12px; color: var(--color-text-secondary); }
</style>
