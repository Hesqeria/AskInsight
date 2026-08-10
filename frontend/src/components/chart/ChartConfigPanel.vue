<template>
  <div class="chart-config-panel">
    <el-form :model="local" label-width="80px" size="small">
      <el-form-item label="图表标题">
        <el-input v-model="local.title" placeholder="输入标题" />
      </el-form-item>
      <el-form-item label="图表类型">
        <chart-type-selector v-model="local.type" />
      </el-form-item>
      <el-form-item label="维度列">
        <el-select v-model="local.categoryKey" placeholder="选择维度列" clearable>
          <el-option v-for="col in columns" :key="col" :label="col" :value="col" />
        </el-select>
      </el-form-item>
      <el-form-item label="指标列">
        <el-select v-model="local.valueKeys" multiple placeholder="选择指标列" clearable>
          <el-option v-for="col in numericColumns" :key="col" :label="col" :value="col" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" size="small" @click="apply">应用</el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup>
import { reactive, computed, watch } from 'vue'
import ChartTypeSelector from './ChartTypeSelector.vue'

const props = defineProps({
  modelValue: { type: Object, default: () => ({ type: 'bar', categoryKey: '', valueKeys: [] }) },
  rows: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'apply'])

const local = reactive({
  type: props.modelValue.type || 'bar',
  categoryKey: props.modelValue.categoryKey || '',
  valueKeys: props.modelValue.valueKeys ? [...props.modelValue.valueKeys] : [],
  title: props.modelValue.title || '',
})

watch(() => props.modelValue, (val) => {
  local.type = val.type || 'bar'
  local.categoryKey = val.categoryKey || ''
  local.valueKeys = val.valueKeys ? [...val.valueKeys] : []
  local.title = val.title || ''
}, { deep: true })

const columns = computed(() => {
  if (!props.rows.length) return []
  return Object.keys(props.rows[0])
})

const numericColumns = computed(() => {
  return columns.value.filter(k => {
    return props.rows.some(r => {
      const v = r[k]
      return typeof v === 'number' || (v !== '' && v != null && !isNaN(Number(v)))
    })
  })
})

function apply() {
  emit('update:modelValue', { ...local })
  emit('apply', { ...local })
}
</script>

<style scoped>
.chart-config-panel { padding: 12px; }
</style>
