<template>
  <div class="main-content">
    <div class="dashboard-header">
      <h2>Dashboard</h2>
      <div class="dashboard-actions">
        <button class="btn" @click="showTemplates = true">
          使用模板
        </button>
        <button class="btn primary" :disabled="!cards.length" @click="showAdd = true">
          + 添加图表
        </button>
        <button class="btn" :disabled="!widgets.length" @click="exportDashboard">
          导出 JSON
        </button>
        <label class="btn file-label">
          导入 JSON
          <input type="file" accept=".json" @change="importDashboard" />
        </label>
        <button class="btn" :disabled="!widgets.length" @click="exportPDF">
          导出 PDF
        </button>
      </div>
    </div>

    <filter-bar v-model="filters" :columns="allColumns" @apply="onFilterApply" />

    <div v-if="!widgets.length" class="empty-state">
      <div class="empty-icon">AskInsight</div>
      <p>暂无保存结果，前往对话后可将结果添加到看板，或使用模板快速创建。</p>
      <div class="empty-actions">
        <router-link to="/" class="go-chat">去对话</router-link>
        <button class="btn" @click="showTemplates = true">使用模板</button>
      </div>
    </div>

    <div v-else ref="gridContainer" class="grid-container">
      <dashboard-grid
        :widgets="widgets"
        :rows="filteredRows"
        :filters="filters"
        :drill-paths="drill.state.paths"
        @update:layout="onLayoutUpdate"
        @config="openConfig"
        @remove="removeWidget"
        @drill-down="onDrillDown"
        @drill-navigate="onDrillNavigate"
      />
    </div>

    <add-widget-dialog v-model="showAdd" :rows="allRows" @add="addWidget" />

    <el-dialog v-model="showConfig" title="图表配置" width="560px" destroy-on-close>
      <chart-config-panel v-if="editingWidget" v-model="editingWidget.config" :rows="editingWidget.rows || allRows" @apply="onConfigApply" />
      <template #footer>
        <el-button size="small" @click="showConfig = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showTemplates" title="选择 Dashboard 模板" width="640px" destroy-on-close>
      <template-gallery @select="applyTemplate" />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import DashboardGrid from '../components/dashboard/DashboardGrid.vue'
import AddWidgetDialog from '../components/dashboard/AddWidgetDialog.vue'
import ChartConfigPanel from '../components/chart/ChartConfigPanel.vue'
import FilterBar from '../components/dashboard/FilterBar.vue'
import TemplateGallery from '../components/dashboard/TemplateGallery.vue'
import { useDashboardFilters } from '../composables/useDashboardFilters.js'
import { useChartExport } from '../composables/useChartExport.js'
import { useDashboardTemplates } from '../composables/useDashboardTemplates.js'
import { useDrillDown } from '../composables/useDrillDown.js'

const STORAGE_KEY = 'askinsight_dashboard'
function loadCards() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

const cards = ref(loadCards())
const showAdd = ref(false)
const showConfig = ref(false)
const showTemplates = ref(false)
const editingWidget = ref(null)
const filters = ref([])
const gridContainer = ref(null)
const { applyFilters } = useDashboardFilters()
const { downloadDashboardPDF } = useChartExport()
const { applyTemplate: getTemplateWidgets } = useDashboardTemplates()
const drill = useDrillDown()

function normalizeCard(card, idx) {
  const keys = card.columns || (card.rows?.length ? Object.keys(card.rows[0]) : [])
  const categoryKey = card.config?.categoryKey || keys[0] || ''
  const valueKeys = card.config?.valueKeys || keys.slice(1)
  return {
    ...card,
    id: card.id || `${Date.now()}-${idx}`,
    x: card.x ?? (idx % 2) * 6,
    y: card.y ?? Math.floor(idx / 2) * 4,
    w: card.w ?? 6,
    h: card.h ?? 4,
    config: {
      title: card.config?.title || card.query || 'Saved result',
      type: card.config?.type || (card.type === 'chart' ? 'bar' : card.type === 'card' ? 'big_number' : 'table'),
      categoryKey,
      valueKeys,
    },
    rows: card.rows || [],
    layout: true,
  }
}

const widgets = computed(() => cards.value.map(normalizeCard))
const allRows = computed(() => widgets.value.flatMap(w => w.rows || []))
const allColumns = computed(() => {
  if (!allRows.value.length) return []
  return Object.keys(allRows.value[0])
})
const filteredRows = computed(() => applyFilters(allRows.value, filters.value))

let saveTimer = null
function save() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cards.value))
    saveTimer = null
  }, 300)
}

function addWidget(widget) {
  cards.value = [...cards.value, widget]
  save()
}

function removeWidget(widget) {
  cards.value = cards.value.filter(c => String(c.id) !== String(widget.id))
  save()
}

function openConfig(widget) {
  editingWidget.value = widget
  showConfig.value = true
}

function onConfigApply() {
  const idx = cards.value.findIndex(c => String(c.id) === String(editingWidget.value?.id))
  if (idx > -1) {
    cards.value[idx] = { ...cards.value[idx], config: { ...editingWidget.value.config } }
    save()
  }
  showConfig.value = false
}

function onFilterApply() {
  // Filters are v-modeled into `filters` by FilterBar; nothing else to
  // do here. We keep the handler so the apply event stays observable
  // (e.g. for analytics or to clear drill-down state below).
  // Drill-down paths are scoped to a specific widget/value chain, so
  // they survive normal filter changes.
}

function onLayoutUpdate(patch) {
  let changed = false
  cards.value = cards.value.map((card) => {
    const p = patch[String(card.id)]
    if (p && (card.x !== p.x || card.y !== p.y || card.w !== p.w || card.h !== p.h)) {
      changed = true
      return { ...card, x: p.x, y: p.y, w: p.w, h: p.h }
    }
    return card
  })
  if (changed) save()
}

function onDrillDown({ widget, field, value }) {
  // Record the drill step for this widget so DrillBreadcrumb can render
  // the path and the user can navigate back.
  const id = String(widget?.id)
  if (!id) return
  drill.push(id, field, value)
  // Drill-down also implies a filter on the rest of the dashboard so
  // related widgets narrow their context too. Append as a dashboard
  // filter (deduped by field+value+operator).
  const exists = filters.value.some(
    f => f.field === field && f.operator === 'eq' && f.value === value)
  if (!exists) {
    filters.value = [...filters.value, { field, operator: 'eq', value }]
  }
}

function onDrillNavigate({ widget, idx }) {
  const id = String(widget?.id)
  if (!id) return
  // Drop drill-down filters that were added after the clicked crumb.
  const path = drill.getPath(id)
  const removed = path.slice(idx + 1)
  drill.slice(id, idx)
  if (removed.length) {
    filters.value = filters.value.filter(f =>
      !removed.some(r => r.field === f.field && r.value === f.value && f.operator === 'eq'))
  }
}

function applyTemplate(key) {
  const templateWidgets = getTemplateWidgets(key, allRows.value)
  cards.value = [...cards.value, ...templateWidgets]
  save()
  showTemplates.value = false
}

function exportDashboard() {
  const data = JSON.stringify(widgets.value, null, 2)
  const blob = new Blob([data], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'askinsight-dashboard.json'
  a.click()
  URL.revokeObjectURL(url)
}

function importDashboard(e) {
  const file = e.target.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = (ev) => {
    try {
      const data = JSON.parse(ev.target.result)
      if (Array.isArray(data)) {
        cards.value = data
        save()
      }
    } catch {}
  }
  reader.readAsText(file)
}

async function exportPDF() {
  await nextTick()
  const widgetsEls = gridContainer.value?.querySelectorAll('.dashboard-widget') || []
  await downloadDashboardPDF(Array.from(widgetsEls), 'askinsight-dashboard.pdf')
}
</script>

<style scoped>
.dashboard-header { display: flex; align-items: center; justify-content: space-between; padding: 24px 32px 16px; }
.dashboard-header h2 { font-size: var(--font-size-xl); font-weight: 700; color: var(--color-text); margin: 0; }
.dashboard-actions { display: flex; align-items: center; gap: 8px; }
.btn { padding: 6px 14px; border: 1px solid var(--color-border); border-radius: var(--radius); background: var(--color-bg); color: var(--color-text); cursor: pointer; font-size: 13px; }
.btn.primary { background: var(--color-primary); color: #fff; border-color: var(--color-primary); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.file-label { position: relative; overflow: hidden; display: inline-block; }
.file-label input { position: absolute; left: -9999px; }
.grid-container { padding: 16px 32px; }
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 50vh; gap: 12px; }
.empty-icon { font-size: 40px; }
.empty-state p { color: var(--color-text-secondary); font-size: var(--font-size-base); }
.empty-actions { display: flex; gap: 12px; align-items: center; }
.go-chat { padding: 8px 20px; border-radius: var(--radius); background: var(--color-primary); color: #fff; text-decoration: none; font-size: var(--font-size-base); }
</style>
