<template>
  <el-dialog v-model="visible" title="添加图表" width="520px" destroy-on-close>
    <el-form :model="form" label-width="80px" size="small">
      <el-form-item label="标题">
        <el-input v-model="form.title" placeholder="图表标题" />
      </el-form-item>
      <el-form-item label="图表类型">
        <chart-type-selector v-model="form.type" />
      </el-form-item>
      <el-form-item label="维度列">
        <el-select v-model="form.categoryKey" placeholder="选择维度列" clearable>
          <el-option v-for="col in columns" :key="col" :label="col" :value="col" />
        </el-select>
      </el-form-item>
      <el-form-item label="指标列">
        <el-select v-model="form.valueKeys" multiple placeholder="选择指标列" clearable>
          <el-option v-for="col in numericColumns" :key="col" :label="col" :value="col" />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button size="small" @click="visible = false">取消</el-button>
      <el-button type="primary" size="small" @click="confirm">确定</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, computed, ref, watch } from 'vue'
import ChartTypeSelector from '../chart/ChartTypeSelector.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  rows: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'add'])

const visible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const form = reactive({
  title: '',
  type: 'bar',
  categoryKey: '',
  valueKeys: [],
})

watch(visible, (val) => {
  if (val) {
    form.title = ''
    form.type = 'bar'
    form.categoryKey = columns.value[0] || ''
    form.valueKeys = numericColumns.value.slice(0, 1)
  }
})

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

let idCounter = 0
function confirm() {
  emit('add', {
    id: `${Date.now()}-${++idCounter}`,
    x: 0, y: 0, w: 6, h: 4,
    config: { ...form },
    rows: [...props.rows],
  })
  visible.value = false
}
</script>
