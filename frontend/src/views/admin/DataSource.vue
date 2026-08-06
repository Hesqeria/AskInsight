<template>
  <div class="page">
    <h2>Data Sources</h2>
    <p class="desc">Configure and probe multiple data sources: Doris (primary), MySQL / PostgreSQL (multi-source extensions).</p>
    <div class="card" v-for="ds in sources" :key="ds.name">
      <div class="row">
        <span class="name">{{ ds.name }}</span>
        <span class="badge" :class="ds.status">{{ ds.status }}</span>
      </div>
      <p class="detail">{{ ds.detail }}</p>
    </div>
    <div class="card">
      <h3>Schema auto-discovery</h3>
      <button class="primary" @click="discover" :disabled="discovering">
        {{ discovering ? 'Scanning...' : 'Scan Doris (dw) Schema' }}
      </button>
      <div v-if="discoverResult" class="msg">
        {{ discoverResult.status }}: {{ discoverResult.tables || 0 }} tables found.
        <span v-if="discoverResult.tables_list">({{ discoverResult.tables_list.join(', ') }})</span>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  data() {
    return {
      discovering: false,
      discoverResult: null,
      sources: [
        { name: 'Apache Doris', status: 'primary', detail: 'SQL execution engine (port 9030). Knowledge base metadata is built from its schema.' },
        { name: 'MySQL', status: 'optional', detail: 'Multi-source extension via app/repositories/mysql (meta_config_mysql.yaml).' },
        { name: 'PostgreSQL', status: 'optional', detail: 'Multi-source extension via app/repositories/pg (meta_config_pg.yaml).' },
        { name: 'Milvus', status: 'primary', detail: 'Vector store for field/metric recall (port 19530).' },
        { name: 'Redis', status: 'primary', detail: 'Cache / session / rate-limit (port 6379).' },
      ],
    };
  },
  methods: {
    async discover() {
      this.discovering = true; this.discoverResult = null;
      try {
        const r = await fetch('/api/admin/schema/discover?db_name=dw', {
          method: 'POST', headers: { Authorization: 'Bearer ' + localStorage.getItem('token') },
        });
        this.discoverResult = await r.json();
      } catch (e) { this.discoverResult = { status: 'error', message: e.message }; }
      finally { this.discovering = false; }
    },
  },
};
</script>

<style scoped>
.page { max-width: 800px; margin: 0 auto; padding: 20px; }
.desc { color: #555; }
.card { background: #fff; border: 1px solid #eee; border-radius: 8px; padding: 16px; margin: 12px 0; }
.row { display: flex; align-items: center; gap: 10px; }
.name { font-weight: 600; }
.badge { padding: 1px 8px; border-radius: 10px; font-size: 11px; font-weight: 700; }
.badge.primary { background: #e6f4ff; color: #1890ff; }
.badge.optional { background: #fff7e6; color: #d48806; }
.detail { color: #555; font-size: 13px; line-height: 1.6; margin: 8px 0 0; }
button.primary { padding: 8px 20px; border: none; border-radius: 6px; background: #1890ff; color: #fff; cursor: pointer; }
.msg { margin-top: 10px; color: #1890ff; font-size: 13px; }
</style>
