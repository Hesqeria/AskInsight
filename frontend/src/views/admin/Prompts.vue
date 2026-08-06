<template>
  <div class="page">
    <h2>Prompts & LLM</h2>
    <p class="desc">Runtime LLM provider switching and the prompt files used by the agent pipeline.</p>

    <div class="card">
      <h3>LLM Provider</h3>
      <div class="row">
        <select v-model="selected">
          <option v-for="p in providers" :key="p" :value="p">{{ p }}</option>
        </select>
        <button class="primary" @click="switchProvider" :disabled="switching || !selected">
          {{ switching ? 'Switching...' : 'Switch' }}
        </button>
        <span v-if="switchMsg" class="msg">{{ switchMsg }}</span>
      </div>
      <p class="detail">Prompt templates live in <code>backend/prompts/</code>: generate_sql, filter_table, correct_sql, etc.</p>
    </div>

    <div class="card">
      <h3>Prompt files</h3>
      <ul>
        <li v-for="f in promptFiles" :key="f">{{ f }}</li>
      </ul>
    </div>
  </div>
</template>

<script>
export default {
  data() {
    return {
      providers: [], selected: '', switching: false, switchMsg: '',
      promptFiles: ['generate_sql.prompt', 'filter_table_info.prompt', 'filter_metric_info.prompt', 'correct_sql.prompt', 'extend_keywords_for_column_recall.prompt', 'extend_keywords_for_metric_recall.prompt', 'extend_keywords_for_value_recall.prompt'],
    };
  },
  async mounted() {
    try {
      const r = await fetch('/api/admin/llm/providers', { headers: { Authorization: 'Bearer ' + localStorage.getItem('token') } });
      const d = await r.json();
      this.providers = d.providers || [];
      this.selected = this.providers[0] || '';
    } catch (e) { this.switchMsg = 'Failed to load providers: ' + e.message; }
  },
  methods: {
    async switchProvider() {
      this.switching = true; this.switchMsg = '';
      try {
        const r = await fetch('/api/admin/llm/switch', {
          method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('token') },
          body: JSON.stringify({ provider: this.selected }),
        });
        const d = await r.json();
        this.switchMsg = d.status === 'ok' ? `Switched to ${d.provider}` : (d.message || 'Failed');
      } catch (e) { this.switchMsg = 'Request failed: ' + e.message; }
      finally { this.switching = false; }
    },
  },
};
</script>

<style scoped>
.page { max-width: 800px; margin: 0 auto; padding: 20px; }
.desc { color: #555; }
.card { background: #fff; border: 1px solid #eee; border-radius: 8px; padding: 16px; margin: 12px 0; }
.card h3 { margin: 0 0 12px; font-size: 15px; }
.row { display: flex; align-items: center; gap: 10px; }
select { padding: 6px 10px; border: 1px solid #ddd; border-radius: 6px; }
button.primary { padding: 8px 20px; border: none; border-radius: 6px; background: #1890ff; color: #fff; cursor: pointer; }
button.primary:disabled { opacity: 0.5; }
.msg { color: #1890ff; font-size: 13px; }
.detail { color: #555; font-size: 13px; margin-top: 12px; }
ul { margin: 0; padding-left: 20px; color: #444; line-height: 1.8; }
</style>
