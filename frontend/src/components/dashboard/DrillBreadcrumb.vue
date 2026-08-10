<template>
  <div class="drill-breadcrumb">
    <span v-for="(item, idx) in path" :key="idx" class="crumb">
      <span class="crumb-text" @click="emit('navigate', idx)">{{ item.field }}: {{ item.value }}</span>
      <span v-if="idx < path.length - 1" class="crumb-sep">&gt;</span>
    </span>
  </div>
</template>

<script setup>
defineProps({
  path: { type: Array, default: () => [] },
})
const emit = defineEmits(['navigate'])
</script>

<style scoped>
.drill-breadcrumb { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--color-text-secondary); padding: 4px 0; }
.crumb-text { cursor: pointer; }
.crumb-text:hover { color: var(--color-primary); text-decoration: underline; }
.crumb-sep { margin: 0 4px; }
</style>
