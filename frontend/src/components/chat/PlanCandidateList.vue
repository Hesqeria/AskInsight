<template>
  <div class="candidate-list">
    <div class="cl-header">
      <span class="cl-icon">🧭</span>
      <span class="cl-title">我理解了多个可能方案,您想查询哪个?</span>
    </div>
    <div class="cl-sub">每个方案都是完整的查询理解,点击选中后可直接执行</div>

    <!-- Ranked candidate cards -->
    <button
      v-for="(cand, idx) in candidates"
      :key="cand._candidate_id"
      class="candidate-card"
      :class="{ active: selectedId === cand._candidate_id }"
      @click="selectCandidate(cand)"
    >
      <!-- Ranking badge + score -->
      <div class="cand-top">
        <span class="rank-badge">方案 {{ idx + 1 }}</span>
        <span v-if="cand._score" class="score-badge">
          推荐度 {{ (cand._score * 100).toFixed(0) }}
        </span>
        <span v-if="idx === 0" class="best-tag">最佳</span>
      </div>

      <!-- Human-readable explanation -->
      <div class="cand-explain">{{ cand._explain }}</div>

      <!-- Confidence + signals -->
      <div class="cand-meta">
        <span class="meta-item">置信度 {{ (cand.confidence * 100).toFixed(0) }}%</span>
        <span v-if="cand._signals" class="meta-item">
          相关度 {{ (cand._signals.rerank * 100).toFixed(0) }}
        </span>
        <span v-if="cand._signals && cand._signals.history" class="meta-item history">
          您常用
        </span>
      </div>

      <!-- SQL preview (collapsible on click, small) -->
      <div v-if="selectedId === cand._candidate_id" class="sql-preview">
        <pre>{{ cand.sql_preview }}</pre>
      </div>
    </button>

    <!-- Selected state actions -->
    <div v-if="selectedCandidate" class="candidate-actions">
      <el-button size="small" :disabled="store.submitting" @click="handleCancel">取消</el-button>
      <el-button
        size="small"
        type="primary"
        :loading="store.submitting"
        @click="confirmSelection"
      >
        执行此方案
      </el-button>
    </div>

    <!-- Error -->
    <div v-if="store.error" class="error-msg">{{ store.error }}</div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useClarifyStore } from '../../stores/clarify.js'

const props = defineProps({
  card: { type: Object, required: true },
})

const emit = defineEmits(['confirmed', 'cancelled'])
const store = useClarifyStore()

const candidates = computed(() => props.card.candidates || [])
const selectedId = ref(null)
const selectedCandidate = computed(() =>
  candidates.value.find(c => c._candidate_id === selectedId.value) || null,
)

function selectCandidate(cand) {
  selectedId.value = selectedId.value === cand._candidate_id ? null : cand._candidate_id
}

async function confirmSelection() {
  if (!selectedCandidate.value) return
  // Call the store with response_type=candidate.
  store.select('_candidate_id', selectedCandidate.value._candidate_id)
  try {
    await store.submitCandidate(selectedCandidate.value._candidate_id)
    emit('confirmed')
  } catch (e) {
    // error surfaced via store.error
  }
}

function handleCancel() {
  store.cancel()
  emit('cancelled')
}
</script>

<style scoped>
.candidate-list { max-width: 640px; }
.cl-header { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 15px; }
.cl-icon { font-size: 18px; }
.cl-sub { color: #909399; font-size: 13px; margin: 4px 0 12px; }
.candidate-card {
  display: block; width: 100%; text-align: left;
  border: 1px solid #e4e7ed; border-radius: 10px;
  padding: 12px 14px; margin-bottom: 10px;
  background: #fff; cursor: pointer; transition: all 0.15s;
}
.candidate-card:hover { border-color: #409eff; }
.candidate-card.active { border-color: #409eff; background: #f5f9ff; }
.cand-top { display: flex; align-items: center; gap: 8px; }
.rank-badge {
  font-size: 12px; padding: 2px 8px; border-radius: 4px;
  background: #409eff; color: #fff;
}
.score-badge { font-size: 12px; color: #909399; }
.best-tag { font-size: 11px; color: #e6a23c; border: 1px solid #e6a23c; border-radius: 4px; padding: 0 5px; }
.cand-explain { margin-top: 8px; font-size: 14px; color: #303133; line-height: 1.5; }
.cand-meta { margin-top: 8px; display: flex; gap: 12px; font-size: 12px; color: #909399; }
.meta-item.history { color: #67c23a; }
.sql-preview {
  margin-top: 10px; padding: 8px 10px; background: #f8f9fa;
  border-radius: 6px; max-height: 120px; overflow: auto;
}
.sql-preview pre { font-size: 12px; color: #555; white-space: pre-wrap; margin: 0; }
.candidate-actions { margin-top: 12px; display: flex; justify-content: flex-end; gap: 8px; }
.error-msg { margin-top: 10px; color: #f56c6c; font-size: 13px; }
</style>
