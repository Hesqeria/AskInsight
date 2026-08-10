<template>
  <div class="filter-bar">
    <div v-for="(filter, idx) in filters" :key="idx" class="filter-item">
      <select v-model="filter.field" class="filter-field" @change="update">
        <option value="">选择字段</option>
        <option v-for="col in columns" :key="col" :value="col">{{ col }}</option>
      </select>
      <select v-model="filter.operator" class="filter-op" @change="update">
        <option value="eq">等于</option>
        <option value="neq">不等于</option>
        <option value="contains">包含</option>
        <option value="gt">大于</option>
        <option value="lt">小于</option>
      </select>
      <input v-model="filter.value" class="filter-input" placeholder="值" @input="update" />
      <button class="filter-remove" @click="removeFilter(idx)">×</button>
    </div>
    <button class="filter-add" @click="addFilter">+ 添加筛选</button>
    <button class="filter-apply" @click="apply">应用</button>
  </div>
</template>

<script setup>
import { reactive, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  columns: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'apply'])

const filters = reactive(props.modelValue?.length ? [...props.modelValue] : [])

watch(() => props.modelValue, (val) => {
  const incoming = val || []
  if (incoming.length !== filters.length || incoming.some((f, i) => f !== filters[i])) {
    filters.splice(0, filters.length, ...incoming)
  }
}, { deep: true })

function addFilter() {
  filters.push({ field: '', operator: 'eq', value: '' })
  update()
}

function removeFilter(idx) {
  filters.splice(idx, 1)
  update()
}

function update() {
  emit('update:modelValue', filters.slice())
}

function apply() {
  update()
  emit('apply', filters.filter(f => f.field && f.value !== ''))
}
</script>

<style scoped>
.filter-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 12px 32px; border-bottom: 1px solid var(--color-border); }
.filter-item { display: flex; align-items: center; gap: 4px; }
.filter-field, .filter-op, .filter-input { padding: 4px 8px; border: 1px solid var(--color-border); border-radius: 4px; font-size: 12px; background: var(--color-bg); color: var(--color-text); }
.filter-input { width: 120px; }
.filter-remove { padding: 4px 8px; background: transparent; border: none; cursor: pointer; color: var(--color-text-muted); }
.filter-add, .filter-apply { padding: 4px 12px; border: 1px solid var(--color-border); border-radius: 4px; background: var(--color-bg); cursor: pointer; font-size: 12px; color: var(--color-text); }
.filter-apply { background: var(--color-primary); color: #fff; border-color: var(--color-primary); }
</style>
