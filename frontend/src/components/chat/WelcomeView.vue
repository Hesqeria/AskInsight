<template>
  <div class="welcome-view">
    <div class="welcome-icon">AskInsight</div>
    <h1 class="welcome-title">{{ title }}</h1>
    <p class="welcome-subtitle">{{ subtitle }}</p>
    <div class="example-grid">
      <div v-for="(ex, i) in currentExamples" :key="i" class="example-card" @click="emit('quickAsk', ex.query)">
        <div class="card-icon">{{ ex.icon }}</div>
        <div class="card-label">{{ ex.label }}</div>
        <div class="card-desc">{{ ex.desc }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ locale: { type: String, default: 'en' } })
const emit = defineEmits(['quickAsk'])

const examplesMap = {
  en: {
    title: 'AskInsight',
    subtitle: 'Ask questions about your data in natural language',
    examples: [
      { icon: 'AskInsight', label: 'Revenue by Region', desc: 'Top regions by sales amount', query: 'Top regions by sales' },
      { icon: 'AskInsight', label: 'Brand Share', desc: 'Brand sales proportion', query: 'Brand sales share' },
      { icon: 'AskInsight', label: 'Top Customers', desc: 'Top 3 customers by spend', query: 'Top 3 customers by spend' },
      { icon: 'AskInsight', label: 'Monthly Trend', desc: 'Monthly sales trend over time', query: 'Monthly sales trend' },
      { icon: 'AskInsight', label: 'High-Value Users', desc: 'High-value customers by category', query: 'High-value customer categories' },
      { icon: 'AskInsight', label: 'Member Tiers', desc: 'Member tier comparison', query: 'Member tier comparison' },
    ]
  },
  cn: {
    title: 'AskInsight',
    subtitle: 'AskInsight',
    examples: [
      { icon: 'AskInsight', label: 'AskInsight', desc: 'AskInsight', query: 'AskInsight' },
      { icon: 'AskInsight', label: 'AskInsight', desc: 'AskInsight', query: 'AskInsight' },
      { icon: 'AskInsight', label: 'AskInsight TOP3 AskInsight', desc: 'AskInsight TOP3', query: 'AskInsight TOP3 AskInsight' },
      { icon: 'AskInsight', label: 'AskInsight', desc: 'AskInsight', query: 'AskInsight' },
      { icon: 'AskInsight', label: 'AskInsight', desc: 'AskInsight', query: 'AskInsight' },
      { icon: 'AskInsight', label: 'AskInsight', desc: 'AskInsight', query: 'AskInsight' },
    ]
  },
}

const current = computed(() => examplesMap[props.locale] || examplesMap.en)
const title = computed(() => current.value.title)
const subtitle = computed(() => current.value.subtitle)
const currentExamples = computed(() => current.value.examples)
</script>

<style scoped>
.welcome-view { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 50vh; padding: 40px 20px; }
.welcome-icon { font-size: 40px; margin-bottom: 12px; }
.welcome-title { font-size: var(--font-size-2xl); font-weight: 700; color: var(--color-text); margin: 0 0 6px; }
.welcome-subtitle { font-size: var(--font-size-base); color: var(--color-text-secondary); margin: 0 0 28px; }
.example-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; max-width: 720px; width: 100%; }
.example-card { padding: 14px; border-radius: var(--radius-lg); border: 1px solid var(--color-border); background: var(--color-bg); cursor: pointer; transition: all var(--transition); }
.example-card:hover { border-color: var(--color-primary); box-shadow: var(--shadow); transform: translateY(-1px); }
.card-icon { font-size: 20px; margin-bottom: 6px; }
.card-label { font-size: var(--font-size-base); color: var(--color-text); font-weight: 500; }
.card-desc { font-size: var(--font-size-sm); color: var(--color-text-secondary); margin-top: 2px; }
</style>
