<template>
  <div class="result-card">
    <details v-if="sql" class="sql-details">
      <summary>Show SQL</summary>
      <pre class="sql-block"><code>{{ sql }}</code></pre>
    </details>

    <div v-if="type === 'table' || type === 'table-with-sql'" class="table-wrap">
      <div class="table-toolbar">
        <input v-model="filterText" placeholder="Filter..." class="table-filter" />
        <span class="row-count">{{ filteredRows.length }} rows</span>
      </div>
      <table class="result-table">
        <thead>
          <tr>
            <th v-for="col in columns" :key="col" @click="toggleSort(col)" class="sortable">
              {{ col }}
              <span v-if="sortCol === col" class="sort-arrow">{{ sortAsc ? '^' : 'v' }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in paginatedRows" :key="i">
            <td v-for="col in columns" :key="col">{{ formatCell(row[col]) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="totalPages > 1" class="pagination">
        <button :disabled="page <= 1" @click="page--">&lt;</button>
        <span>{{ page }} / {{ totalPages }}</span>
        <button :disabled="page >= totalPages" @click="page++">&gt;</button>
      </div>
    </div>

    <div class="action-bar">
      <button class="action-btn" @click="copySQL" v-if="sql">Copy SQL</button>
      <button class="action-btn" @click="exportData">Export CSV</button>
      <button class="action-btn" @click="emit('favorite')">{{ isFav ? 'Unstar' : 'Star' }}</button>
      <button class="action-btn primary" @click="emit('pin')">Pin to Dashboard</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useExport } from '../../composables/useExport.js'

const props = defineProps({
  sql: { type: String, default: '' },
  columns: { type: Array, default: () => [] },
  rows: { type: Array, default: () => [] },
  type: { type: String, default: 'table' },
  isFav: { type: Boolean, default: false },
})

const emit = defineEmits(['favorite', 'pin'])
const { exportCSV, copyToClipboard } = useExport()

const filterText = ref('')
const sortCol = ref('')
const sortAsc = ref(true)
const page = ref(1)
const pageSize = 20

const filteredRows = computed(() => {
  let rows = props.rows
  if (filterText.value) {
    const q = filterText.value.toLowerCase()
    rows = rows.filter(r => props.columns.some(c => String(r[c] || '').toLowerCase().includes(q)))
  }
  if (sortCol.value) {
    rows = [...rows].sort((a, b) => {
      const va = a[sortCol.value], vb = b[sortCol.value]
      if (typeof va === 'number' && typeof vb === 'number') return sortAsc.value ? va - vb : vb - va
      return sortAsc.value ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va))
    })
  }
  return rows
})

const totalPages = computed(() => Math.ceil(filteredRows.value.length / pageSize) || 1)
const paginatedRows = computed(() => {
  const start = (page.value - 1) * pageSize
  return filteredRows.value.slice(start, start + pageSize)
})

watch(filterText, () => { page.value = 1 })

function toggleSort(col) {
  if (sortCol.value === col) sortAsc.value = !sortAsc.value
  else { sortCol.value = col; sortAsc.value = true }
}

function formatCell(val) {
  if (val === null || val === undefined) return '-'
  if (typeof val === 'number') return val.toLocaleString()
  return val
}

function copySQL() { copyToClipboard(props.sql) }
function exportData() { exportCSV(props.columns, props.rows) }
</script>

<style scoped>
.result-card { margin-top: 8px; }
.sql-details { margin-bottom: 10px; }
.sql-details summary { cursor: pointer; font-size: 13px; color: var(--color-primary); }
.sql-block { background: var(--color-bg-tertiary); border: 1px solid var(--color-border); border-radius: var(--radius); padding: 10px; font-size: 12px; overflow-x: auto; max-height: 160px; white-space: pre-wrap; word-break: break-all; }
.table-toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.table-filter { padding: 4px 10px; border: 1px solid var(--color-border); border-radius: var(--radius); font-size: 12px; flex: 1; max-width: 200px; background: var(--color-bg); color: var(--color-text); }
.row-count { font-size: 12px; color: var(--color-text-secondary); }
.result-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.result-table th, .result-table td { border: 1px solid var(--color-border); padding: 6px 10px; text-align: left; }
.result-table th { background: var(--color-bg-tertiary); font-weight: 600; white-space: nowrap; }
.sortable { cursor: pointer; user-select: none; }
.sortable:hover { background: var(--color-bg-secondary); }
.sort-arrow { margin-left: 4px; }
.pagination { display: flex; gap: 8px; align-items: center; justify-content: center; margin-top: 10px; font-size: 13px; }
.pagination button { padding: 4px 10px; border: 1px solid var(--color-border); border-radius: var(--radius); background: var(--color-bg); cursor: pointer; color: var(--color-text); }
.pagination button:disabled { opacity: 0.3; cursor: default; }
.action-bar { display: flex; gap: 6px; margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--color-border-light); }
.action-btn { padding: 4px 10px; border: 1px solid var(--color-border); border-radius: var(--radius); background: var(--color-bg); color: var(--color-text-secondary); cursor: pointer; font-size: 12px; transition: all var(--transition); }
.action-btn:hover { border-color: var(--color-primary); color: var(--color-primary); }
.action-btn.primary { background: var(--color-primary); color: #fff; border-color: var(--color-primary); }
</style>
