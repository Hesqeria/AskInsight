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
          <span v-if="msg.role === 'assistant'" class="avatar-icon">{{ 'Ask' }}</span>
          <span v-else class="avatar-icon">{{ 'U' }}</span>
        </div>
        <div class="bubble">
          <MessageBubble :msg="msg" />

          <template v-if="msg.type === 'chart'">
            <ChartRenderer
              :type="autoChartType(msg)"
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
            :is-fav="isFavorite(msg)"
            @favorite="toggleFavoriteForMsg(msg)"
            @pin="pinToDashboard(msg)"
          />
        </div>
      </div>
      <div class="spacer"></div>
    </div>

    <InputBar
      :loading="loading"
      :suggestions="suggestions"
      @send="(q) => sendQuestion(q)"
      @quick-ask="(q) => sendQuestion(q)"
    />
  </div>
</template>

<script setup>
import { ref, nextTick, watch, computed } from 'vue'
import MessageBubble from './MessageBubble.vue'
import ChartRenderer from '../chart/ChartRenderer.vue'
import ResultCard from './ResultCard.vue'
import InputBar from './InputBar.vue'
import WelcomeView from './WelcomeView.vue'
import { useChat } from '../../composables/useChat.js'
import { useHistory } from '../../composables/useHistory.js'
import { useI18n } from '../../utils/i18n.js'

const { question, loading, messages, sendQuestion } = useChat()
const { queryHistory, favQueries, toggleFavorite, isFavorite } = useHistory()
const { locale, t } = useI18n()
const messagesEl = ref(null)
const menuOpen = ref(false)

const suggestions = computed(() => {
  const m = {
    en: ['Top regions by sales', 'Brand sales share', 'Top 3 customers by spend', 'Monthly sales trend', 'High-value customers', 'Member tier comparison'],
    cn: ['AskInsight', 'AskInsight', 'AskInsight TOP3 AskInsight', 'AskInsight', 'AskInsight', 'AskInsight'],
  }
  return m[locale.value] || m.en
})

function autoChartType(msg) {
  const keys = Object.keys(msg.rows[0] || {})
  const numKeys = keys.filter(k => typeof msg.rows[0][k] === 'number')
  if (numKeys.length === 1 && msg.rows.length > 10) return 'line'
  if (msg.rows.length <= 6 && numKeys.length === 1) return 'pie'
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
.messages { flex: 1; overflow-y: auto; padding: 20px 15% 160px; }
.spacer { height: 120px; }
</style>
