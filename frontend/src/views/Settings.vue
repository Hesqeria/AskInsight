<template>
  <div class="settings">
    <h2>{{ t('settings') }}</h2>

    <div class="section">
      <h3>{{ t('llmModel') }}</h3>
      <select v-model="model" @change="save">
        <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
      </select>
    </div>

    <div class="section">
      <h3>{{ t('datasource') }}</h3>
      <select v-model="datasource" @change="save">
        <option v-for="d in datasources" :key="d" :value="d">{{ d }}</option>
      </select>
    </div>

    <div class="section">
      <h3>{{ t('language') }}</h3>
      <select v-model="lang" @change="onLangChange">
        <option value="en">English</option>
        <option value="cn">AskInsight</option>
      </select>
    </div>

    <div class="section">
      <h3>Dark Mode</h3>
      <label class="toggle-label">
        <input type="checkbox" v-model="darkMode" @change="toggleDark" />
        <span class="toggle-text">Enable dark theme</span>
      </label>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from '../utils/i18n.js'
const { locale, t } = useI18n()

const models = ['deepseek-chat', 'gpt-4', 'gpt-3.5-turbo']
const datasources = ['doris', 'mysql', 'postgresql']
const model = ref(localStorage.getItem('askinsight_model') || 'deepseek-chat')
const datasource = ref(localStorage.getItem('askinsight_ds') || 'doris')
const lang = ref(locale.value)
const darkMode = ref(localStorage.getItem('askinsight_dark') === 'true')

onMounted(() => {
  if (darkMode.value) document.documentElement.setAttribute('data-theme', 'dark')
})

function save() {
  localStorage.setItem('askinsight_model', model.value)
  localStorage.setItem('askinsight_ds', datasource.value)
}

function onLangChange() {
  locale.value = lang.value
  save()
}

function toggleDark() {
  const isDark = darkMode.value
  localStorage.setItem('askinsight_dark', isDark)
  document.documentElement.setAttribute('data-theme', isDark ? 'dark' : '')
}
</script>

<style scoped>
.settings { max-width: 600px; margin: 40px auto; padding: 20px; }
h2 { font-size: var(--font-size-xl); margin-bottom: 24px; color: var(--color-text); }
.section { margin-bottom: 20px; }
h3 { font-size: var(--font-size-base); color: var(--color-text-secondary); margin-bottom: 6px; }
select { padding: 8px 12px; border: 1px solid var(--color-border); border-radius: var(--radius); font-size: var(--font-size-base); width: 100%; max-width: 300px; background: var(--color-bg); color: var(--color-text); }
.toggle-label { display: flex; align-items: center; gap: 10px; cursor: pointer; }
.toggle-label input { width: 16px; height: 16px; cursor: pointer; }
.toggle-text { font-size: var(--font-size-base); color: var(--color-text); }
</style>
