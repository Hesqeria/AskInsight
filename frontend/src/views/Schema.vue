<template>
  <div class="schema">
    <h2>{{ t("schemaBrowser") }}</h2>
    <div class="tree">
      <div v-for="t in tables" :key="t.name" class="table-node">
        <div class="table-header" @click="t._open=!t._open">
          {{ t._open ? '-' : '+' }} {{ t.name }} ({{ t.columns?.length || 0 }} {{ t("cols") }})
        </div>
        <div v-if="t._open" class="columns">
          <div v-for="c in (t.columns||[])" :key="c.name" class="col-row">
            <span class="col-name">{{ c.name }}</span>
            <span class="col-type">{{ c.type }}</span>
            <span class="col-comment">{{ c.comment || c.description || '' }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted } from "vue"
import { useI18n } from "../utils/i18n.js"
const { t } = useI18n();
const tables = ref([]);
onMounted(async () => {
  try {
    const r = await fetch("/api/admin/schema/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json",
        Authorization: "Bearer " + (localStorage.getItem("token") || "") },
      body: JSON.stringify({})
    });
    const d = await r.json();
    if (d.tables) tables.value = d.tables.map(t => ({...t, _open: false}));
  } catch (e) { console.error(e); }
});
</script>
<style scoped>
.schema{max-width:800px;margin:40px auto;padding:20px}
h2{margin-bottom:20px}
.table-node{margin-bottom:8px;border:1px solid #e5e7eb;border-radius:6px}
.table-header{padding:10px 14px;background:#f8f9fb;cursor:pointer;font-weight:600;font-size:14px}
.columns{padding:8px 14px}
.col-row{display:flex;gap:16px;padding:4px 0;font-size:13px;border-bottom:1px solid #f0f0f0}
.col-name{font-weight:600;min-width:140px}
.col-type{color:#409eff;min-width:80px;font-family:monospace}
.col-comment{color:#888;flex:1}
</style>
