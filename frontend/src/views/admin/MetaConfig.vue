<template>
  <div class="page">
    <h2>Metadata Configuration</h2>
    <p class="desc">
      Manage schema metadata (table / column roles, aliases, descriptions) that powers the NL2SQL recall.
      Configured in <code>backend/conf/meta_config.yaml</code>, then rebuilt via <code>make init</code>.
    </p>
    <div class="card">
      <h3>Config files</h3>
      <ul>
        <li><code>conf/meta_config.yaml</code> — default Doris metadata</li>
        <li><code>conf/meta_config_dw.yaml</code> / <code>meta_config_mysql.yaml</code> / <code>meta_config_pg.yaml</code> — multi-source variants</li>
      </ul>
    </div>
    <div class="card">
      <h3>Rebuild knowledge base</h3>
      <button class="primary" @click="rebuild" :disabled="loading">{{ loading ? 'Rebuilding...' : 'Rebuild Knowledge Base' }}</button>
      <p v-if="message" class="msg">{{ message }}</p>
    </div>
  </div>
</template>

<script>
import { apiPost } from '../../utils/api.js'
export default {
  data() { return { loading: false, message: '' } },
  methods: {
    async rebuild() {
      this.loading = true; this.message = '';
      try {
        const d = await apiPost('/api/admin/knowledge/incremental', {});
        
        this.message = d.message || d.status || 'Started';
      } catch (e) { this.message = 'Request failed: ' + e.message; }
      finally { this.loading = false; }
    },
  },
};
</script>

<style scoped>
.page { max-width: 800px; margin: 0 auto; padding: 20px; }
.desc { color: #555; line-height: 1.6; }
.card { background: #fff; border: 1px solid #eee; border-radius: 8px; padding: 16px; margin: 16px 0; }
.card h3 { margin: 0 0 12px; font-size: 15px; }
.card ul { margin: 0; padding-left: 20px; color: #444; line-height: 1.8; }
button.primary { padding: 8px 20px; border: none; border-radius: 6px; background: #1890ff; color: #fff; cursor: pointer; }
button.primary:disabled { opacity: 0.5; }
.msg { margin-top: 10px; color: #1890ff; }
</style>
