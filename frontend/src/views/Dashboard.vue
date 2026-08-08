<template>
  <div class="main-content">
    <div class="dashboard-header">
      <h2>Dashboard</h2>
      <span class="card-count">{{ cards.length }} saved items</span>
    </div>

    <div v-if="cards.length === 0" class="empty-state">
      <div class="empty-icon">AskInsight</div>
      <p>No saved results yet. Ask a question in Chat and pin results here.</p>
      <router-link to="/" class="go-chat">Go to Chat</router-link>
    </div>

    <div v-else class="card-grid">
      <div v-for="card in cards" :key="card.id" class="dash-card">
        <div class="dash-card-header">
          <span class="dash-query">{{ card.query || 'No query' }}</span>
          <button class="dash-close" @click="removeCard(card.id)">x</button>
        </div>

        <div v-if="card.type === 'card'" class="card-value">{{ card.value }}</div>

        <div v-else-if="card.type === 'chart'" class="card-chart">
          <ChartRenderer
            :type="'bar'"
            :category-key="card.categoryKey"
            :value-keys="card.valueKeys"
            :rows="card.rows"
          />
        </div>

        <div v-else class="card-table">
          <table class="mini-table">
            <thead><tr><th v-for="col in (card.columns || []).slice(0, 4)" :key="col">{{ col }}</th></tr></thead>
            <tbody>
              <tr v-for="(row, i) in card.rows.slice(0, 3)" :key="i">
                <td v-for="col in (card.columns || []).slice(0, 4)" :key="col">{{ row[col] }}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="card.rows.length > 3" class="more-rows">+{{ card.rows.length - 3 }} more rows</div>
        </div>

        <div class="dash-time">{{ formatDate(card.timestamp) }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import ChartRenderer from '../components/chart/ChartRenderer.vue'

const cards = ref(JSON.parse(localStorage.getItem('askinsight_dashboard') || '[]'))

function removeCard(id) {
  cards.value = cards.value.filter(c => c.id !== id)
  localStorage.setItem('askinsight_dashboard', JSON.stringify(cards.value))
}

function formatDate(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleString()
}
</script>

<style scoped>
.dashboard-header { display: flex; align-items: center; justify-content: space-between; padding: 24px 32px 16px; }
.dashboard-header h2 { font-size: var(--font-size-xl); font-weight: 700; color: var(--color-text); margin: 0; }
.card-count { font-size: var(--font-size-sm); color: var(--color-text-secondary); }
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 50vh; gap: 12px; }
.empty-icon { font-size: 40px; }
.empty-state p { color: var(--color-text-secondary); font-size: var(--font-size-base); }
.go-chat { padding: 8px 20px; border-radius: var(--radius); background: var(--color-primary); color: #fff; text-decoration: none; font-size: var(--font-size-base); }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; padding: 16px 32px; }
.dash-card { border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-bg); overflow: hidden; transition: box-shadow var(--transition); }
.dash-card:hover { box-shadow: var(--shadow); }
.dash-card-header { display: flex; justify-content: space-between; align-items: center; padding: 12px 14px; border-bottom: 1px solid var(--color-border-light); }
.dash-query { font-size: 13px; color: var(--color-text); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.dash-close { border: none; background: none; cursor: pointer; color: var(--color-text-muted); font-size: 14px; padding: 2px 6px; }
.card-chart { padding: 8px; height: 220px; }
.card-table { padding: 8px; }
.mini-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.mini-table th, .mini-table td { border: 1px solid var(--color-border-light); padding: 4px 8px; text-align: left; }
.mini-table th { background: var(--color-bg-tertiary); }
.more-rows { text-align: center; font-size: 11px; color: var(--color-text-secondary); padding: 4px; }
.dash-time { padding: 8px 14px; font-size: 11px; color: var(--color-text-muted); border-top: 1px solid var(--color-border-light); }
</style>
