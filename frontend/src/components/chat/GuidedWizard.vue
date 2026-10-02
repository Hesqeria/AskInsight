<template>
  <div class="guided-wizard">
    <div class="wizard-header">
      <span class="wizard-icon">🧭</span>
      <span class="wizard-title">让我帮您一步步明确问题</span>
    </div>
    <div class="wizard-sub">您的问题比较模糊,我会分几步引导您完成查询设置</div>

    <!-- Step indicator -->
    <el-steps :active="step" finish-status="success" align-center class="wizard-steps">
      <el-step title="选择指标" />
      <el-step title="选择时间" />
      <el-step title="选择分组" />
    </el-steps>

    <!-- Step 0: measure -->
    <div v-if="step === 0" class="wizard-body">
      <div class="step-question">您想看哪个指标?</div>
      <div class="option-list">
        <button
          v-for="opt in measureOptions"
          :key="opt.value"
          class="wizard-option"
          :class="{ active: draft.measure === opt.value }"
          @click="draft.measure = opt.value"
        >
          <div class="opt-main">
            <span class="opt-label">{{ opt.label }}</span>
            <span v-if="opt.recommended" class="rec-tag">推荐</span>
          </div>
          <div v-if="opt.description" class="opt-desc">{{ opt.description }}</div>
        </button>
      </div>
    </div>

    <!-- Step 1: time -->
    <div v-if="step === 1" class="wizard-body">
      <div class="step-question">哪个时间范围?</div>
      <div class="option-grid">
        <button
          v-for="opt in timeOptions"
          :key="opt.value"
          class="wizard-option"
          :class="{ active: draft.time === opt.value }"
          @click="draft.time = opt.value"
        >
          <span class="opt-label">{{ opt.label }}</span>
          <span v-if="opt.description" class="opt-desc">{{ opt.description }}</span>
        </button>
      </div>
    </div>

    <!-- Step 2: group_by -->
    <div v-if="step === 2" class="wizard-body">
      <div class="step-question">需要按维度分组吗?(可多选,可选跳过)</div>
      <div class="option-list">
        <button
          v-for="opt in groupByOptions"
          :key="String(opt.value)"
          class="wizard-option"
          :class="{ active: draft.group_by.includes(opt.value) }"
          @click="toggleGroupBy(opt)"
        >
          <span class="opt-label">{{ opt.label }}</span>
          <span v-if="opt.description" class="opt-desc">{{ opt.description }}</span>
        </button>
      </div>
      <div class="skip-hint">不选择则查看总量</div>
    </div>

    <!-- Error -->
    <div v-if="store.error" class="error-msg">{{ store.error }}</div>

    <!-- Actions -->
    <div class="wizard-actions">
      <el-button v-if="step > 0" size="small" @click="step--">上一步</el-button>
      <el-button size="small" @click="handleCancel">取消</el-button>
      <el-button
        v-if="step < 2"
        size="small"
        type="primary"
        :disabled="!canNext"
        @click="step++"
      >
        下一步
      </el-button>
      <el-button
        v-if="step === 2"
        size="small"
        type="primary"
        :loading="store.submitting"
        :disabled="!draft.measure"
        @click="finish"
      >
        完成并执行
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { useClarifyStore } from '../../stores/clarify.js'

const props = defineProps({
  card: { type: Object, required: true },
})

const emit = defineEmits(['cancelled', 'confirmed'])
const store = useClarifyStore()

const step = ref(0)
const draft = reactive({
  measure: null,
  time: null,
  group_by: [],
})

const measureOptions = computed(() => {
  const g = (props.card.suggestions || []).find(x => x.field === 'measure')
  return g ? g.options : []
})
const timeOptions = computed(() => {
  const g = (props.card.suggestions || []).find(x => x.field === 'time')
  return g ? g.options : []
})
const groupByOptions = computed(() => {
  const g = (props.card.suggestions || []).find(x => x.field === 'group_by')
  return g ? g.options.filter(o => o.value !== null) : []
})

const canNext = computed(() => {
  if (step.value === 0) return !!draft.measure
  if (step.value === 1) return !!draft.time
  return true
})

function toggleGroupBy(opt) {
  const idx = draft.group_by.indexOf(opt.value)
  if (idx >= 0) draft.group_by.splice(idx, 1)
  else draft.group_by.push(opt.value)
}

function handleCancel() {
  store.cancel()
  emit('cancelled')
}

async function finish() {
  store.select('measure', draft.measure)
  store.select('time', draft.time)
  if (draft.group_by.length) store.setGroupBy(draft.group_by)
  try {
    await store.submitResponse({ responseType: 'selection', confirmed: true })
    emit('confirmed')
  } catch (e) {
    // Error surfaced via store.error.
  }
}
</script>

<style scoped>
.guided-wizard {
  border: 1px solid #e4e7ed; border-radius: 12px; padding: 20px;
  background: #fff; max-width: 560px;
}
.wizard-header { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 15px; }
.wizard-icon { font-size: 20px; }
.wizard-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.wizard-steps { margin: 16px 0 20px; }
.wizard-body { min-height: 120px; }
.step-question { font-size: 14px; color: #303133; margin-bottom: 12px; }
.option-list { display: flex; flex-direction: column; gap: 8px; }
.option-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.wizard-option {
  border: 1px solid #dcdfe6; border-radius: 8px; padding: 10px 14px;
  background: #fff; cursor: pointer; text-align: left;
  transition: all 0.15s;
}
.option-grid .wizard-option { width: auto; }
.wizard-option:hover { border-color: #409eff; }
.wizard-option.active { border-color: #409eff; background: #ecf5ff; }
.opt-main { display: flex; align-items: center; gap: 8px; }
.opt-label { font-size: 14px; }
.opt-desc { font-size: 12px; color: #909399; }
.rec-tag { font-size: 10px; color: #67c23a; }
.skip-hint { color: #c0c4cc; font-size: 12px; margin-top: 8px; }
.error-msg { margin-top: 10px; color: #f56c6c; font-size: 13px; }
.wizard-actions { margin-top: 16px; display: flex; justify-content: flex-end; gap: 8px; }
</style>
