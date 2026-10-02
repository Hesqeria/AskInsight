<template>
  <div class="clarify-card">
    <div class="clarify-header">
      <span class="clarify-icon">🤔</span>
      <span class="clarify-title">{{ $t ? $t('clarify.title') || '我需要您再确认一下' : '我需要您再确认一下' }}</span>
    </div>

    <div class="clarify-question" v-if="card && card.question">
      {{ card.question }}
    </div>

    <!-- Current understanding summary -->
    <div class="clarify-summary" v-if="card && card.current_summary">
      <span class="summary-label">当前理解:</span>
      <span class="summary-text">{{ card.current_summary }}</span>
    </div>

    <!-- Suggestion groups -->
    <div
      v-for="group in card.suggestions || []"
      :key="group.field"
      class="suggestion-group"
    >
      <div class="group-prompt">
        {{ group.prompt }}
        <span v-if="group.multi_select" class="group-tag">可多选</span>
        <span v-else class="group-tag">必选</span>
      </div>

      <!-- Selection options (buttons for multi, radio-like for single) -->
      <div class="option-grid" :class="{ 'multi': group.multi_select }">
        <button
          v-for="opt in group.options"
          :key="String(opt.value)"
          class="option-btn"
          :class="{
            'selected': isSelected(group.field, opt),
            'recommended': opt.recommended,
          }"
          @click="toggleOption(group, opt)"
        >
          <span class="option-label">{{ opt.label }}</span>
          <span v-if="opt.description" class="option-desc">{{ opt.description }}</span>
          <span v-if="opt.recommended" class="rec-tag">推荐</span>
        </button>
      </div>

      <!-- Custom/free-text input for fields that allow it -->
      <div v-if="group.allow_custom" class="custom-row">
        <el-input
          v-model="customInputs[group.field]"
          size="small"
          placeholder="输入其他选项..."
          @keyup.enter="submitCustom(group)"
        />
        <el-button size="small" type="primary" plain @click="submitCustom(group)">
          确定
        </el-button>
      </div>
    </div>

    <!-- Error message -->
    <div v-if="store.error" class="clarify-error">{{ store.error }}</div>

    <!-- Actions -->
    <div class="clarify-actions">
      <el-button size="small" :disabled="store.submitting" @click="handleCancel">
        取消
      </el-button>
      <el-button
        size="small"
        type="primary"
        :loading="store.submitting"
        :disabled="!canConfirm"
        @click="handleConfirm"
      >
        确认并执行
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, computed } from 'vue'
import { useClarifyStore } from '../../stores/clarify.js'

const props = defineProps({
  card: { type: Object, required: true },
  onConfirmed: { type: Function, default: null },
  onCancelled: { type: Function, default: null },
})

const store = useClarifyStore()
const customInputs = reactive({})

// Initialize store selection state from the card.
function initSelections() {
  const groups = props.card.suggestions || []
  for (const g of groups) {
    if (g.field === 'group_by') {
      store.setGroupBy([])
    } else {
      store.select(g.field, null)
    }
  }
}
initSelections()

const canConfirm = computed(() => {
  // At least one suggestion must be non-empty.
  const groups = props.card.suggestions || []
  if (!groups.length) return true
  // For group_by (optional multi), confirm if user made ANY choice OR
  // explicitly picked "不分组". Single-select fields require a value.
  const selections = store.selections
  const hasAny = Object.values(selections).some(v => v !== null && v !== undefined && v !== '')
  return hasAny
})

function isSelected(group, opt) {
  const field = group.field
  const cur = store.selections[field]
  if (Array.isArray(cur)) return cur.includes(opt.value)
  return cur === opt.value
}

function toggleOption(group, opt) {
  const field = group.field
  if (group.multi_select) {
    // Multi-select: toggle in array; "不分组" (value null) clears all.
    if (opt.value === null) {
      store.setGroupBy([])
      return
    }
    const cur = Array.isArray(store.selections[field]) ? [...store.selections[field]] : []
    const idx = cur.indexOf(opt.value)
    if (idx >= 0) cur.splice(idx, 1)
    else cur.push(opt.value)
    store.setGroupBy(cur)
  } else {
    store.select(field, opt.value)
  }
}

function submitCustom(group) {
  const val = (customInputs[group.field] || '').trim()
  if (!val) return
  store.select(group.field, val)
  customInputs[group.field] = ''
}

async function handleConfirm() {
  await store.submitResponse({ responseType: 'selection', confirmed: true })
  if (props.onConfirmed && store.activeCard === null) {
    props.onConfirmed()
  }
}

async function handleCancel() {
  await store.cancel()
  if (props.onCancelled) props.onCancelled()
}
</script>

<style scoped>
.clarify-card {
  border: 1px solid #e4e7ed;
  border-radius: 12px;
  padding: 16px 20px;
  background: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  margin: 8px 0;
  max-width: 640px;
}
.clarify-header { display: flex; align-items: center; gap: 8px; font-weight: 600; }
.clarify-icon { font-size: 18px; }
.clarify-question { margin-top: 8px; font-size: 14px; color: #606266; }
.clarify-summary {
  margin-top: 8px; padding: 8px 12px; background: #f5f7fa;
  border-radius: 8px; font-size: 13px;
}
.summary-label { color: #909399; }
.summary-text { color: #303133; }
.suggestion-group { margin-top: 14px; }
.group-prompt { font-size: 14px; color: #303133; margin-bottom: 8px; display: flex; gap: 6px; align-items: center; }
.group-tag {
  font-size: 11px; padding: 1px 6px; border-radius: 4px;
  background: #ecf5ff; color: #409eff;
}
.option-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.option-grid.multi .option-btn { width: auto; }
.option-btn {
  border: 1px solid #dcdfe6; background: #fff; border-radius: 8px;
  padding: 8px 14px; cursor: pointer; text-align: left;
  display: flex; flex-direction: column; gap: 2px;
  transition: all 0.15s;
}
.option-btn:hover { border-color: #409eff; }
.option-btn.selected { border-color: #409eff; background: #ecf5ff; color: #409eff; }
.option-btn.recommended { border-style: dashed; }
.option-label { font-size: 14px; }
.option-desc { font-size: 12px; color: #909399; }
.rec-tag { font-size: 10px; color: #67c23a; }
.custom-row { margin-top: 8px; display: flex; gap: 8px; max-width: 320px; }
.clarify-error { margin-top: 10px; color: #f56c6c; font-size: 13px; }
.clarify-actions { margin-top: 16px; display: flex; justify-content: flex-end; gap: 8px; }
</style>
