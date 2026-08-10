<template>
  <div class="chart-type-selector">
    <el-radio-group v-model="selected" size="small" @change="onChange">
      <el-radio-button v-for="(label, key) in CHART_TYPES" :key="key" :label="key">
        {{ label }}
      </el-radio-button>
    </el-radio-group>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useCharts } from '../../composables/useCharts.js'

const props = defineProps({
  modelValue: { type: String, default: 'bar' },
})
const emit = defineEmits(['update:modelValue', 'change'])

const { CHART_TYPES } = useCharts()
const selected = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

function onChange(val) {
  emit('change', val)
}
</script>

<style scoped>
.chart-type-selector { padding: 8px 0; }
</style>
