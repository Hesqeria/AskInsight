<template>
  <div class="page">
    <h2>Glossary</h2>
    <p class="desc">Business term -> standard table/column mappings injected into SQL generation.</p>
    <div v-if="loading" class="msg">Loading...</div>
    <div v-else-if="error" class="msg error">{{ error }}</div>
    <table v-else-if="terms.length" class="table">
      <thead>
        <tr><th>Term</th><th>Standard Name</th><th>Table</th><th>Column</th><th>Description</th></tr>
      </thead>
      <tbody>
        <tr v-for="t in terms" :key="t.term + t.standard_name">
          <td>{{ t.term }}</td>
          <td>{{ t.standard_name }}</td>
          <td>{{ t.table_name }}</td>
          <td>{{ t.column_name }}</td>
          <td>{{ t.description }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else class="msg">No glossary terms found.</p>
  </div>
</template>

<script>
export default {
  data() { return { terms: [], loading: true, error: '' } },
  async mounted() {
    try {
      const r = await fetch('/api/glossary', { headers: { Authorization: 'Bearer ' + localStorage.getItem('token') } });
      const d = await r.json();
      this.terms = d.terms || [];
    } catch (e) { this.error = 'Failed to load glossary: ' + e.message; }
    finally { this.loading = false; }
  },
};
</script>

<style scoped>
.page { max-width: 900px; margin: 0 auto; padding: 20px; }
.desc { color: #555; }
.table { width: 100%; border-collapse: collapse; background: #fff; }
.table th, .table td { border: 1px solid #e5e7eb; padding: 8px 10px; font-size: 13px; text-align: left; }
.table th { background: #f8fafc; }
.msg { color: #888; }
.msg.error { color: #e74c3c; }
</style>
