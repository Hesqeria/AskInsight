<template>
  <div class="approval-card">
    <div class="ac-head">
      <span class="ac-badge">{{ t('approval.pending') }}</span>
      <span class="ac-ticket">{{ card.ticket_id }}</span>
    </div>
    <p class="ac-reason">{{ card.reason || 'SQL 引用高敏感(PII L>=3)字段,需人工审批后执行。' }}</p>
    <ul v-if="violations.length" class="ac-violations">
      <li v-for="(v, i) in violations" :key="i">
        <code>{{ v.column_ref }}</code>
        <span class="ac-class">{{ v.class_name }} · L{{ v.pii_level }}</span>
      </li>
    </ul>
    <div v-if="deciding" class="ac-status">提交中…</div>
    <div v-else-if="canDecide" class="ac-actions">
      <el-button size="small" type="danger" plain @click="decide('rejected')">{{ t('approval.reject') }}</el-button>
      <el-button size="small" type="primary" @click="approve">{{ t('approval.approve') }}</el-button>
    </div>
    <div v-else class="ac-status">{{ t('approval.waiting') }}</div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useInboxStore } from '../../stores/inbox.js'
import { useI18n } from '../../utils/i18n.js'

const props = defineProps({
  card: { type: Object, required: true },
  onDecided: { type: Function, required: true },
})
const { t } = useI18n()
const inbox = useInboxStore()
const deciding = ref(false)

const violations = computed(() => props.card.violations || [])
const canDecide = computed(() => inbox.canApprove)

async function decide(decision) {
  deciding.value = true
  try { await props.onDecided(decision) } finally { deciding.value = false }
}
async function approve() { await decide('approved') }
</script>

<style scoped>
.approval-card { border: 1px solid rgba(230,162,60,0.5); border-radius: var(--radius-lg, 10px); padding: 14px 16px; background: rgba(230,162,60,0.06); margin: 8px 0; }
.ac-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.ac-badge { background: #e6a23c; color: #fff; font-size: 12px; padding: 2px 8px; border-radius: var(--radius-round, 999px); font-weight: 600; }
.ac-ticket { font-size: 11px; color: var(--color-text-secondary); font-family: monospace; }
.ac-reason { font-size: 13px; color: var(--color-text); margin: 0 0 8px; line-height: 1.5; }
.ac-violations { margin: 0 0 10px; padding-left: 18px; font-size: 12px; color: var(--color-text-secondary); }
.ac-violations li { margin-bottom: 2px; }
.ac-violations code { background: var(--color-bg-tertiary); padding: 1px 5px; border-radius: 4px; }
.ac-class { margin-left: 6px; opacity: 0.8; }
.ac-actions { display: flex; gap: 8px; justify-content: flex-end; }
.ac-status { font-size: 12px; color: var(--color-text-secondary); text-align: right; }
</style>
