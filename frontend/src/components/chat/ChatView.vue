<template>
  <div class="main-content">
    <button class="hamburger" @click="menuOpen = !menuOpen"><span></span><span></span><span></span></button>

    <WelcomeView
      v-if="messages.length === 0 && !loading"
      :locale="locale"
      @quick-ask="sendQuestion"
    />

    <div v-else ref="messagesEl" class="messages">
      <div v-for="(msg, index) in messages" :key="index" :class="['message-row', msg.role]">
        <div class="avatar">
          <span v-if="msg.role === 'assistant'" class="avatar-icon">问</span>
          <span v-else class="avatar-icon">我</span>
        </div>
        <div class="bubble">
          <div v-if="msg.role === 'user' && !loading" class="msg-actions">
            <button class="msg-action-btn" title="重新提问"
                    @click="sendQuestion(msg.content)">重试</button>
          </div>
          <div v-else-if="msg.role === 'assistant' && msg.content && !loading"
               class="msg-actions">
            <button class="msg-action-btn" title="复制回答"
                    @click="copyAnswer(msg)">复制</button>
          </div>
          <MessageBubble :msg="msg" />

          <template v-if="msg.type === 'chart'">
            <div class="chart-switch-row">
              <button v-for="ct in ['bar', 'line', 'pie', 'table']" :key="ct"
                      class="chart-switch-btn"
                      :class="{ active: (msg.chartType || autoChartType(msg)) === ct }"
                      @click="msg.chartType = ct">
                {{ chartTypeLabel(ct) }}
              </button>
            </div>
            <ChartRenderer
              :type="msg.chartType || autoChartType(msg)"
              :category-key="msg.categoryKey"
              :value-keys="msg.valueKeys"
              :rows="msg.rows"
            />
          </template>

          <ResultCard
            v-if="msg.type === 'table' || msg.type === 'table-with-sql'"
            :sql="msg.sql || ''"
            :columns="msg.columns || []"
            :rows="msg.rows || []"
            :type="msg.type"
            :spill-id="msg.spillId || ''"
            :total-rows="msg.totalRows || 0"
            :is-fav="isFavorite(msg)"
            @favorite="toggleFavoriteForMsg(msg)"
            @pin="pinToDashboard(msg)"
          />

          <div v-if="msg.type === 'note'" class="note-block">
            <div class="note-text">{{ msg.content }}</div>
            <div v-if="msg.suggestions && msg.suggestions.length" class="suggest-row">
              <button v-for="q in msg.suggestions" :key="q"
                      class="suggest-chip" @click="sendQuestion(q)">{{ q }}</button>
            </div>
          </div>

          <div v-if="msg.type === 'metric'" class="metric-card">
            <div class="metric-label">{{ msg.metric }}</div>
            <div class="metric-value">{{ formatMetric(msg.value, msg.unit) }}</div>
            <div class="metric-text">{{ msg.text }}</div>
          </div>

          <div v-if="msg.type === 'insight'" class="insight-block">
            <div class="insight-title">洞察解读</div>
            <div class="insight-text">{{ msg.content }}</div>
          </div>

          <ClarifyCard
            v-if="msg.type === 'clarify' && !isVague(msg) && !hasCandidates(msg)"
            :card="msg.card"
            :on-confirmed="onClarifyConfirmed"
            :on-cancelled="onClarifyCancelled"
          />

          <GuidedWizard
            v-if="msg.type === 'clarify' && isVague(msg) && !hasCandidates(msg)"
            :card="msg.card"
            @cancelled="onClarifyCancelled"
            @confirmed="onClarifyConfirmed"
          />

          <!-- Multi-path candidate list (多路重排) takes priority -->
          <PlanCandidateList
            v-if="msg.type === 'clarify' && hasCandidates(msg)"
            :card="msg.card"
            @confirmed="onClarifyConfirmed"
            @cancelled="onClarifyCancelled"
          />

          <el-button
            v-if="msg.type === 'clarify'"
            size="small"
            text
            type="primary"
            class="open-editor-btn"
            @click="openPlanEditor(msg)"
          >
            高级调整
          </el-button>

          <ApprovalCard
            v-if="msg.type === 'approval'"
            :card="msg.card"
            :on-decided="(d) => onApprovalDecided(msg, d)"
          />

          <PlanEditorDrawer
            v-model="editorOpen"
            :card="activeEditorCard"
            @confirmed="onClarifyConfirmed"
          />
        </div>
      </div>
      <div class="spacer"></div>
    </div>

    <div class="chat-toolbar" v-if="lastRequestId || messages.length">
      <el-button size="small" text type="primary" @click="timelineOpen = true">
        {{ t('timeline') }}
      </el-button>
      <el-button size="small" text type="primary" @click="newConversation()">
        新建对话
      </el-button>
      <el-button size="small" text type="primary" @click="sessionsOpen = true; sessionsList = loadSessions()">
        历史会话
      </el-button>
    </div>

    <el-drawer v-model="sessionsOpen" title="历史会话" size="360px">
      <div v-if="!sessionsList.length" class="sessions-empty">暂无历史会话</div>
      <div v-for="sess in [...sessionsList].reverse()" :key="sess.id" class="session-item">
        <div class="session-main" @click="sessionsOpen = false; restoreSession(sess.id)">
          <div class="session-title">{{ sess.title }}</div>
          <div class="session-time">{{ sess.at }}</div>
        </div>
        <button class="session-del" @click.stop="deleteSession(sess.id); sessionsList = loadSessions()">删除</button>
      </div>
    </el-drawer>

    <InputBar
      :loading="loading"
      :suggestions="suggestions"
      @send="(q) => sendQuestion(q)"
      @quick-ask="(q) => sendQuestion(q)"
    />

    <SessionTimeline
      :open="timelineOpen"
      :session-id="lastRequestId"
      @close="timelineOpen = false"
    />
  </div>
</template>

<script setup>
import { ref, nextTick, watch, computed } from 'vue'
import MessageBubble from './MessageBubble.vue'
import ChartRenderer from '../chart/ChartRenderer.vue'
import ResultCard from './ResultCard.vue'
import ClarifyCard from './ClarifyCard.vue'
import GuidedWizard from './GuidedWizard.vue'
import PlanCandidateList from './PlanCandidateList.vue'
import PlanEditorDrawer from './PlanEditorDrawer.vue'
import InputBar from './InputBar.vue'
import WelcomeView from './WelcomeView.vue'
import ApprovalCard from './ApprovalCard.vue'
import SessionTimeline from './SessionTimeline.vue'
import { useChat } from '../../composables/useChat.js'
import { useInboxStore } from '../../stores/inbox.js'
import { useHistory } from '../../composables/useHistory.js'
import { useI18n } from '../../utils/i18n.js'

const { question, loading, messages, sendQuestion, resumeClarify, resumeApproval, lastRequestId , newConversation, loadSessions, restoreSession, deleteSession } = useChat()
const inbox = useInboxStore()
const timelineOpen = ref(false)
  const sessionsOpen = ref(false)
  const sessionsList = ref([])
const { queryHistory, favQueries, toggleFavorite, isFavorite } = useHistory()
const { locale, t } = useI18n()
inbox.startPolling()
const messagesEl = ref(null)
const menuOpen = ref(false)
const editorOpen = ref(false)
const activeEditorCard = ref(null)

// A vague question (confidence < 0.3) shows the step-by-step wizard.
// Signal: the card's missing_fields includes a missing measure.
function isVague(msg) {
  const fields = msg.card?.missing_fields || []
  return fields.some(f => f.field === 'measure' && f.reason === 'missing')
}

// Multi-path candidates take priority over wizard/card.
function hasCandidates(msg) {
  const cands = msg.card?.candidates || []
  return cands.length > 0
}

function openPlanEditor(msg) {
  activeEditorCard.value = msg.card
  editorOpen.value = true
}

// After clarification is confirmed, re-run the original question so the
// agent generates SQL from the merged (now high-confidence) plan.
function onClarifyConfirmed() {
  const clarifyMsg = messages.value.find(m => m.type === 'clarify')
  if (!clarifyMsg || !clarifyMsg.card) return
  // M3: resume the suspended turn via the inbox respond endpoint
  // (streams generate_sql -> ... -> END) instead of re-running the
  // whole graph from intent recognition.
  const resumed = resumeClarify(clarifyMsg.card)
  if (resumed) {
    if (clarifyMsg.card.inbox_id) inbox.removeById(clarifyMsg.card.inbox_id)
    const idx = messages.value.findIndex(m => m.type === 'clarify')
    if (idx >= 0) messages.value.splice(idx, 1)
    return
  }
  // Legacy fallback: no inbox id -> re-run the question.
  if (clarifyMsg.card.question) sendQuestion(clarifyMsg.card.question)
}

function onApprovalDecided(msg, decision) {
  const card = msg.card || {}
  resumeApproval(card, decision)
  const idx = messages.value.indexOf(msg)
  if (idx >= 0) {
    messages.value.splice(idx, 1, {
      ...msg, type: 'reply',
      content: decision === 'approved'
        ? '已批准,继续执行…'
        : '该查询已被拒绝执行。',
    })
  }
}

function onClarifyCancelled() {
  const idx = messages.value.findIndex(m => m.type === 'clarify')
  if (idx >= 0) messages.value.splice(idx, 1)
}

const suggestions = computed(() => {
  const m = {
    en: ['Top regions by sales', 'Brand sales share', 'Top 3 customers by spend', 'Monthly sales trend', 'High-value customers', 'Member tier comparison'],
    cn: ['AskInsight', 'AskInsight', 'AskInsight TOP3 AskInsight', 'AskInsight', 'AskInsight', 'AskInsight'],
  }
  return m[locale.value] || m.en
})

function formatMetric(v, unit) {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  if (Number.isNaN(n)) return String(v)
  const body = n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  return unit ? `${body} ${unit}` : body
}

const CHART_LABELS = { bar: '柱状', line: '折线', pie: '饼图', table: '表格' }
function chartTypeLabel(ct) { return CHART_LABELS[ct] || ct }

function copyAnswer(msg) {
  const text = typeof msg.content === 'string'
    ? msg.content
    : JSON.stringify(msg.content, null, 2)
  if (navigator.clipboard) navigator.clipboard.writeText(text)
}

function autoChartType(msg) {
  const isNum = v => typeof v === 'number' ||
    (v !== null && v !== undefined && v !== '' && !isNaN(Number(v)))
  const keys = Object.keys(msg.rows[0] || {})
  const numKeys = keys.filter(k => isNum(msg.rows[0][k]))
  const catKeys = keys.filter(k => !numKeys.includes(k))
  const isTime = catKeys.length > 0 &&
    /(dt|date|day|month|week|year|时间|日期|天|月|年)/i.test(catKeys[0])
  if (numKeys.length === 1 && (isTime || msg.rows.length > 10)) return 'line'
  if (msg.rows.length <= 6 && numKeys.length === 1 && !isTime) return 'pie'
  if (numKeys.length >= 2) return 'stacked_bar'
  return 'bar'
}

function toggleFavoriteForMsg(msg) {
  const key = msg.sql || msg.content || ''
  if (key) toggleFavorite(key)
}

function pinToDashboard(msg) {
  const card = {
    id: Date.now(),
    query: msg.content || '',
    sql: msg.sql || '',
    type: msg.type,
    columns: msg.columns || [],
    rows: msg.rows || [],
    categoryKey: msg.categoryKey || '',
    valueKeys: msg.valueKeys || [],
    timestamp: new Date().toISOString(),
  }
  const saved = JSON.parse(localStorage.getItem('askinsight_dashboard') || '[]')
  saved.unshift(card)
  localStorage.setItem('askinsight_dashboard', JSON.stringify(saved))
}

watch(messages, () => {
  nextTick(() => {
    if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  })
}, { deep: true })
</script>

<style scoped>
.main-content { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.chat-toolbar { display: flex; justify-content: flex-end; padding: 4px 16px 0; }
.messages { flex: 1; overflow-y: auto; padding: 20px 15% 160px; }
.spacer { height: 120px; }

.insight-block {
  margin-top: 10px;
  padding: 12px 16px;
  background: linear-gradient(135deg, rgba(14,124,107,0.08), rgba(14,124,107,0.02));
  border: 1px solid rgba(14,124,107,0.22);
  border-left: 3px solid #0E7C6B;
  border-radius: 10px;
  max-width: 720px;
}
.insight-title {
  font-size: 12px;
  font-weight: 600;
  color: #0E7C6B;
  letter-spacing: 0.05em;
  margin-bottom: 6px;
}
.insight-text {
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--text-primary, #2A2E2C);
  white-space: pre-wrap;
}

.metric-card {
  margin-top: 10px;
  padding: 18px 24px;
  background: linear-gradient(160deg, rgba(14,124,107,0.10), rgba(14,124,107,0.03));
  border: 1px solid rgba(14,124,107,0.25);
  border-left: 4px solid #0E7C6B;
  border-radius: 12px;
  max-width: 420px;
}
.metric-label {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: #0E7C6B;
  margin-bottom: 4px;
}
.metric-value {
  font-size: 30px;
  font-weight: 700;
  color: var(--text-primary, #2A2E2C);
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.metric-text {
  margin-top: 6px;
  font-size: 12.5px;
  color: var(--text-secondary, #6B7370);
}

.note-block {
  margin-top: 8px;
  padding: 10px 16px;
  background: rgba(14,124,107,0.05);
  border-left: 3px solid rgba(14,124,107,0.5);
  border-radius: 8px;
  max-width: 720px;
}
.note-text {
  font-size: 13px;
  line-height: 1.65;
  color: var(--text-secondary, #6B7370);
}
.suggest-row {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.suggest-chip {
  font-size: 12px;
  padding: 4px 12px;
  border: 1px solid rgba(14,124,107,0.35);
  border-radius: 999px;
  background: rgba(14,124,107,0.06);
  color: #0E7C6B;
  cursor: pointer;
  transition: all 0.15s ease;
}
.suggest-chip:hover {
  background: #0E7C6B;
  color: #fff;
}

.msg-actions { display: flex; gap: 6px; margin-bottom: 4px; opacity: 0.55; }
.msg-actions:hover { opacity: 1; }
.msg-action-btn {
  border: 1px solid var(--border, #dcdfe6); border-radius: 4px;
  background: transparent; color: var(--text, #606266);
  font-size: 11px; padding: 1px 8px; cursor: pointer;
}
.chart-switch-row {
  display: flex;
  gap: 6px;
  margin: 6px 0 2px;
}
.chart-switch-btn {
  font-size: 11px;
  padding: 2px 10px;
  border: 1px solid var(--border-color, #D8DEDC);
  border-radius: 999px;
  background: transparent;
  color: var(--text-secondary, #6B7370);
  cursor: pointer;
  transition: all 0.15s ease;
}
.chart-switch-btn.active {
  background: #0E7C6B;
  border-color: #0E7C6B;
  color: #fff;
}

.sessions-empty { color: var(--text-secondary, #6B7370); padding: 24px; text-align: center; font-size: 13px; }
.session-item { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border-color, #E8ECEA); }
.session-main { flex: 1; cursor: pointer; min-width: 0; }
.session-main:hover .session-title { color: #0E7C6B; }
.session-title { font-size: 13px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.session-time { font-size: 11px; color: var(--text-secondary, #9AA3A0); margin-top: 2px; }
.session-del { font-size: 11px; color: #C0392B; background: none; border: none; cursor: pointer; }
</style>
