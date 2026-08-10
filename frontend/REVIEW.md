---
phase: AskInsight Phase 3.12 Visualization PRD
reviewed: 2026-08-09T12:00:00Z
depth: standard
files_reviewed: 18
files_reviewed_list:
  - src/composables/useCharts.js
  - src/composables/useEchartsTheme.js
  - src/composables/useChartConfig.js
  - src/composables/useChartExport.js
  - src/composables/useDashboardFilters.js
  - src/composables/useDrillDown.js
  - src/components/chart/ChartRenderer.vue
  - src/components/chart/ChartTypeSelector.vue
  - src/components/chart/ChartConfigPanel.vue
  - src/components/chart/ChartActions.vue
  - src/components/dashboard/DashboardWidget.vue
  - src/components/dashboard/DashboardGrid.vue
  - src/components/dashboard/AddWidgetDialog.vue
  - src/components/dashboard/FilterBar.vue
  - src/components/dashboard/DrillBreadcrumb.vue
  - src/views/Dashboard.vue
  - src/main.js
  - package.json
review_modes:
  java_backend: false
  vue3_frontend: true
skills_used:
  - nlie-vue3-code-style
  - nlie-vue3-components
findings:
  critical: 5
  warning: 23
  info: 0
  total: 28
status: issues_found
---

# Phase 3.12: AskInsight Visualization Frontend Review

**Reviewed:** 2026-08-09
**Depth:** standard
**Files Reviewed:** 18
**Status:** issues_found
**Review Modes:** Java backend=false, Vue3 frontend=true

## Summary

This review covers the Phase 3.12 visualization frontend for AskInsight: chart composables, dashboard components, export utilities, and the Dashboard view. The code was built with `npm run build` and the production build fails immediately due to a duplicate identifier in `Dashboard.vue`. Beyond the build blocker, several runtime features are non-functional or incorrectly wired: drill-down is not propagated through the widget/grid hierarchy, chart action buttons are invisible due to a scoped CSS mistake, and the treemap chart imports the wrong ECharts component. Several security and robustness issues around SQL string construction, CSV escaping, and localStorage parsing were also found.

## Build / Runtime Results

Command run: `npm run build`

```text
> date-agent-frontend@0.0.0 build
> vite build

vite v7.3.1 building client environment for production...
transforming...
✓ 12 modules transformed.
✗ Build failed in 253ms
error during build:
[vite:vue] [vue/compiler-sfc] Identifier 'applyFilters' has already been declared. (82:9)

D:/大模型/智能问数/AskInsight/frontend/src/views/Dashboard.vue
136|  }
137|
138|  function applyFilters() {
   |           ^
139|    // filters reactive already applied via computed
140|  }
```

The build cannot complete until the duplicate declaration is resolved.

## Critical Issues (BLOCKER)

### CR-01: Duplicate `applyFilters` declaration prevents production build

**Severity:** critical | **Classification:** BLOCKER  
**File:** `src/views/Dashboard.vue:82` and `src/views/Dashboard.vue:138`  
**Issue:** `applyFilters` is first destructured from `useDashboardFilters()` as a `const` and then later declared again as a `function`. This creates a duplicate declaration syntax error that stops the Vite production build.
**Fix:** Remove the empty `function applyFilters()` declaration. The reactive filtering is already applied through the `filteredRows` computed.

```js
// Remove this entire block (lines ~138-140):
function applyFilters() {
  // filters reactive already applied via computed
}
```

### CR-02: Drill-down event chain is not wired

**Severity:** critical | **Classification:** BLOCKER  
**Files:** `src/views/Dashboard.vue:52`, `src/components/dashboard/DashboardGrid.vue`, `src/components/dashboard/DashboardWidget.vue`, `src/components/chart/ChartRenderer.vue`  
**Issue:** `Dashboard.vue` listens for `@drill-down` on `DashboardGrid`, but `DashboardGrid` never declares or emits `drill-down`. `DashboardWidget` also never listens to `ChartRenderer`'s `drill-down` event. As a result, drill-down clicks are swallowed and the feature is non-functional.
**Fix:**
1. In `DashboardWidget.vue`, listen to `ChartRenderer` and re-emit with the widget context:
```vue
<chart-renderer
  ...
  @drill-down="emit('drill-down', { widget: props.widget, ...$event })"
/>
```
2. In `DashboardGrid.vue`, forward the event and add it to `defineEmits`:
```vue
<dashboard-widget
  ...
  @drill-down="$emit('drill-down', $event)"
/>
```
```js
const emit = defineEmits(['update:widgets', 'config', 'remove', 'drill-down'])
```

### CR-03: Chart action buttons are invisible due to scoped CSS

**Severity:** critical | **Classification:** BLOCKER  
**Files:** `src/components/chart/ChartActions.vue:31-34`, `src/components/chart/ChartRenderer.vue`  
**Issue:** The hover rule `.chart-wrap:hover .chart-actions { opacity: 1; pointer-events: auto; }` is defined in the scoped style of `ChartActions.vue`, but `.chart-wrap` is an element rendered by `ChartRenderer.vue`. Scoped styles add a data attribute only to the component's own elements, so the selector never matches and the PNG/CSV/SQL buttons remain permanently invisible and non-interactive.
**Fix:** Move the hover rule to `ChartRenderer.vue` and use `:deep()` to target the child component.

```vue
<style scoped>
.chart-wrap:hover :deep(.chart-actions) {
  opacity: 1;
  pointer-events: auto;
}
</style>
```
Remove the same rule from `ChartActions.vue`.

### CR-04: Treemap chart imports the wrong ECharts component

**Severity:** critical | **Classification:** BLOCKER  
**File:** `src/composables/useCharts.js:4`  
**Issue:** The code imports `TreeChart` from `echarts/charts` but registers a series of type `treemap`. ECharts requires `TreemapChart` for `series: { type: 'treemap' }`; `TreeChart` is for tree diagrams. At runtime, selecting the treemap chart type will throw `Component series.treemap not exists. Load it first.`.
**Fix:** Replace the import and registration.

```js
import {
  BarChart, LineChart, PieChart, ScatterChart,
  RadarChart, FunnelChart, GaugeChart, TreemapChart, HeatmapChart,
} from 'echarts/charts'

echarts.use([
  BarChart, LineChart, PieChart, ScatterChart, RadarChart,
  FunnelChart, GaugeChart, TreemapChart, HeatmapChart,
  ...
])
```

### CR-05: `onDrillDown` expects a `widget` payload that is never emitted

**Severity:** critical | **Classification:** BLOCKER  
**File:** `src/views/Dashboard.vue:142`  
**Issue:** Even after the drill-down event chain is wired, `onDrillDown({ widget, field, value })` expects `widget` to be included in the payload. `ChartRenderer` only emits `{ field, value }`. The `widget` argument will always be `undefined`, breaking any per-widget drill-down or ID tracking.
**Fix:** Emit the widget context from `DashboardWidget` (see CR-02) and consume the ID in the handler.

```js
function onDrillDown({ widget, field, value }) {
  filters.value = [...filters.value, { field, operator: 'eq', value }]
}
```

## High Issues (WARNING)

### HI-01: SQL string construction is vulnerable to injection

**Severity:** high | **Classification:** WARNING  
**Files:** `src/composables/useDashboardFilters.js:35-50`, `src/composables/useDrillDown.js:35-46`  
**Issue:** Both `toSQLWhere` and `buildSQL` assemble SQL by string concatenation. They only escape single quotes, leaving field names unescaped and ignoring other injection vectors (e.g., backslashes, comment syntax). The `buildSQL` WHERE/ORDER BY detection is also fragile against comments or subqueries.
**Fix:** Do not build SQL on the client. Pass filter parameters to the backend and use prepared statements or a query builder. If client-side preview is required, escape field names with backticks/identifiers and bind all values as parameters.

### HI-02: Stacked area chart is not actually stacked

**Severity:** high | **Classification:** WARNING  
**File:** `src/composables/useCharts.js:89, 96-97`  
**Issue:** `case 'stacked_area': return buildLineOption(safeRows, categoryKey, valueKeys, true)` passes the fourth argument as `area`, but `buildLineOption` accepts no `stacked` parameter and does not set `stack: 'total'`. The result is an overlapping area chart, not a stacked area chart.
**Fix:** Add a `stacked` parameter to `buildLineOption` and apply it to each series.

```js
function buildLineOption(rows, categoryKey, valueKeys, area = false, stacked = false) {
  ...
  series: valueKeys.map(k => ({
    type: 'line',
    name: k,
    data: rows.map(r => Number(r[k]) || 0),
    smooth: true,
    symbol: 'circle',
    symbolSize: 6,
    areaStyle: area ? { opacity: 0.3 } : undefined,
    stack: stacked ? 'total' : undefined,
  })),
}
```

### HI-03: `ChartRenderer` exposes a stale chart instance

**Severity:** high | **Classification:** WARNING  
**File:** `src/components/chart/ChartRenderer.vue:13, 23`  
**Issue:** `let chartInstance = null` is assigned after `defineExpose({ chartInstance, ... })` has already captured the initial `null`. Any parent that reads `chartInstance` through a template ref will always get `null`.
**Fix:** Use a `shallowRef` so the exposed value updates.

```js
const chartInstance = shallowRef(null)
// in render():
chartInstance.value = initChart(...)
// in onUnmounted:
chartInstance.value = null
```

### HI-04: Charts do not resize when dashboard widgets are resized

**Severity:** high | **Classification:** WARNING  
**Files:** `src/components/chart/ChartRenderer.vue`, `src/composables/useCharts.js:193-199`  
**Issue:** `useCharts` exports `resizeAll`, but it is never called. When a user resizes a `vue-grid-layout` widget, the chart canvas does not resize and may become clipped or distorted.
**Fix:** Add a `ResizeObserver` on the chart container in `ChartRenderer` and call `chartInstance.value.resize()` (or `resizeAll`) when the container dimensions change.

```js
const resizeObserver = new ResizeObserver(() => chartInstance.value?.resize())
onMounted(() => resizeObserver.observe(chartEl.value))
onUnmounted(() => resizeObserver.disconnect())
```

### HI-05: Theme changes are not applied to charts

**Severity:** high | **Classification:** WARNING  
**Files:** `src/composables/useEchartsTheme.js:46-56`, `src/components/chart/ChartRenderer.vue`  
**Issue:** `useEchartsTheme` exports `watchTheme`, but it is never invoked. Switching the application dark/light theme will not re-theme or re-render existing charts.
**Fix:** Call `watchTheme()` once in `Dashboard.vue` or `ChartRenderer.vue` and re-initialize the chart (or update its option with the new theme colors) when the `data-theme` attribute changes.

### HI-06: SQL copy action is always hidden in dashboard widgets

**Severity:** high | **Classification:** WARNING  
**Files:** `src/components/dashboard/DashboardWidget.vue:20`, `src/components/chart/ChartRenderer.vue:6`  
**Issue:** `ChartRenderer` accepts a `sql` prop that controls the SQL button, but `DashboardWidget` never defines or passes a `sql` prop. The SQL button is therefore never rendered in dashboard widgets, making the copy-SQL feature inaccessible.
**Fix:** Add a `sql` prop to `DashboardWidget` and pass it through to `ChartRenderer`.

```vue
<chart-renderer
  :sql="widget.sql"
  ...
/>
```

### HI-07: Chart title cannot be edited after widget creation

**Severity:** high | **Classification:** WARNING  
**File:** `src/components/chart/ChartConfigPanel.vue`  
**Issue:** The widget config contains a `title` field, but `ChartConfigPanel` only exposes chart type, category dimension, and value metrics. Once a widget is added, its title cannot be changed through the config panel.
**Fix:** Add a title field to the form and sync it with `local`.

```vue
<el-form-item label="图表标题">
  <el-input v-model="local.title" placeholder="输入标题" />
</el-form-item>
```

### HI-08: Dashboard widgets lose their original data source and show the global row set

**Severity:** high | **Classification:** WARNING  
**Files:** `src/views/Dashboard.vue:54-66, 82-95`, `src/components/dashboard/AddWidgetDialog.vue:56`  
**Issue:** The `widgets` computed flattens all saved-card rows into a single `allRows` array and passes `filteredRows` to every widget. `AddWidgetDialog` emits a widget with no `rows` or query reference. Each widget therefore visualizes the same global dataset rather than its own saved question/card. Additionally, the `widgets` getter returns only a subset of card fields (`id`, `x`, `y`, `w`, `h`, `config`, `rows`), discarding original fields like `query` and `type` so they are lost when the dashboard is saved.
**Fix:** Preserve each widget's own `rows` and query/source reference in the card model. Pass the widget's own rows to `ChartRenderer`. Return the original card object augmented with layout/config so that no original metadata is lost.

```js
// widgets getter (simplified)
return cards.value.map((card, idx) => ({
  ...card,
  id: card.id || Date.now() + idx,
  x: card.x ?? (idx % 2) * 6,
  y: card.y ?? Math.floor(idx / 2) * 4,
  w: card.w ?? 6,
  h: card.h ?? 4,
  config: card.config || { ... },
}))
```

## Medium Issues (WARNING)

### ME-01: CSV export quoting is not RFC-4180 compliant

**Severity:** medium | **Classification:** WARNING  
**File:** `src/composables/useChartExport.js:28-30`  
**Issue:** `downloadCSV` uses `JSON.stringify(r[c] ?? '')` to wrap values. JSON escaping uses backslashes, which are not valid CSV escapes; internal quotes are not doubled, and embedded newlines may break the row format.
**Fix:** Use a proper CSV escape function.

```js
function csvEscape(value) {
  const str = String(value ?? '')
  if (/[",\n\r]/.test(str)) return '"' + str.replace(/"/g, '""') + '"'
  return str
}
const lines = rows.map(r => cols.map(c => csvEscape(r[c])).join(','))
```

### ME-02: `copySQL` lacks error handling and fallback

**Severity:** medium | **Classification:** WARNING  
**File:** `src/composables/useChartExport.js:63`  
**Issue:** `navigator.clipboard.writeText(sql)` is called without awaiting the promise or catching errors. In non-secure contexts (HTTP) or when the user denies clipboard permission, the action fails silently.
**Fix:**

```js
async function copySQL(sql) {
  if (!sql) return
  try {
    await navigator.clipboard.writeText(sql)
  } catch (e) {
    // fallback: temporary textarea
    const ta = document.createElement('textarea')
    ta.value = sql
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
}
```

### ME-03: `localStorage` and JSON import parsing can crash the app

**Severity:** medium | **Classification:** WARNING  
**File:** `src/views/Dashboard.vue:33, 146-158`  
**Issue:** `JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')` and the `importDashboard` JSON parse are not wrapped in try/catch. Corrupted localStorage or a malformed JSON file will throw and break the dashboard.
**Fix:** Wrap both parse calls in try/catch and show an error message (e.g., `ElMessage.error`).

```js
const cards = ref((() => {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
  } catch (e) {
    return []
  }
})())
```

### ME-04: Gauge max value is hardcoded to 100

**Severity:** medium | **Classification:** WARNING  
**File:** `src/composables/useCharts.js:174`  
**Issue:** The gauge chart always uses `max: 100`. Values above 100 render beyond the scale; values below 1 use a disproportionate scale.
**Fix:** Compute `max` from the data or make it configurable.

```js
const max = Math.max(100, value * 1.2)
```

### ME-05: Radar max calculation drops zero and negative values

**Severity:** medium | **Classification:** WARNING  
**File:** `src/composables/useCharts.js:139`  
**Issue:** `indicators` use `Math.max(...rows.map(...).filter(Boolean))`. Zeros and negative values are filtered out, so the indicator `max` can be wrong when the data contains them.
**Fix:** Remove the `filter(Boolean)` and use a safe fallback.

```js
max: Math.max(...rows.map(r => Number(r[k]) || 0), 1)
```

### ME-06: Config edits on legacy cards do not persist until the card is re-saved

**Severity:** medium | **Classification:** WARNING  
**File:** `src/views/Dashboard.vue:54-66, 102-106`  
**Issue:** The `widgets` computed getter creates new objects for cards that lack `layout: true`. Editing such a widget mutates the computed copy, not the card in `cards`, so `save()` writes the unchanged card.
**Fix:** Normalize cards on first load so they all carry `layout: true`, or change the getter to return the original card object augmented with layout/config rather than constructing a new object.

### ME-07: FilterBar uses plain HTML controls instead of Element Plus

**Severity:** medium | **Classification:** WARNING  
**File:** `src/components/dashboard/FilterBar.vue`  
**Issue:** The rest of the app uses Element Plus components (`el-select`, `el-input`, `el-button`). `FilterBar` uses plain `<select>`, `<input>`, and `<button>`, which is inconsistent and may not honor the Element Plus theme, sizing, or disabled states.
**Fix:** Replace with Element Plus components.

### ME-08: Dashboard uses a custom modal overlay instead of `el-dialog`

**Severity:** medium | **Classification:** WARNING  
**File:** `src/views/Dashboard.vue:57-69`  
**Issue:** The chart config modal is implemented with a custom overlay and close button, while `AddWidgetDialog` uses `el-dialog`. This duplicates code and misses accessibility, focus trapping, and `esc` close behavior provided by Element Plus.
**Fix:** Replace the custom modal with `el-dialog`.

## Low Issues (WARNING)

### LO-01: `onDrillDown` only adds a filter when no widget is being edited

**Severity:** low | **Classification:** WARNING  
**File:** `src/views/Dashboard.vue:140-143`  
**Issue:** The guard `if (!editingWidget.value)` prevents drill-down from adding a filter while the config panel is open. This appears accidental and will confuse users.
**Fix:** Remove the guard and always add the drill-down filter.

```js
function onDrillDown({ widget, field, value }) {
  filters.value = [...filters.value, { field, operator: 'eq', value }]
}
```

### LO-02: Widget IDs from `Date.now()` can collide

**Severity:** low | **Classification:** WARNING  
**File:** `src/components/dashboard/AddWidgetDialog.vue:62`  
**Issue:** Rapidly adding two widgets within the same millisecond will produce duplicate IDs.
**Fix:** Use a counter or `crypto.randomUUID()`.

```js
let idCounter = 0
id: `${Date.now()}-${++idCounter}`
```

### LO-03: `props.rows` watcher may miss in-place mutations

**Severity:** low | **Classification:** WARNING  
**File:** `src/components/chart/ChartRenderer.vue:91`  
**Issue:** `watch(() => [props.type, props.rows, ...], ...)` with `deep: true` watches the returned array's properties, not the `props.rows` array itself. If a parent mutates `rows` in place rather than replacing it, the chart may not re-render.
**Fix:** Watch `props.rows` directly as a separate watcher, or ensure the parent always replaces the array reference.

### LO-04: `numericColumns` only inspects the first row

**Severity:** low | **Classification:** WARNING  
**Files:** `src/components/chart/ChartConfigPanel.vue:52`, `src/components/dashboard/AddWidgetDialog.vue:69`  
**Issue:** A numeric column that happens to be `null` or a string in the first row is excluded from the value metric selector, even if it is numeric in subsequent rows.
**Fix:** Sample multiple rows (or all rows) to determine numeric columns.

### LO-05: `DashboardWidget.config` is not reactive to replacement

**Severity:** low | **Classification:** WARNING  
**File:** `src/components/dashboard/DashboardWidget.vue:25`  
**Issue:** `const config = props.widget.config || {}` captures the config object at setup time. If the parent later replaces `props.widget.config` with a new object, the local `config` will still reference the old one.
**Fix:** Use a computed property.

```js
const config = computed(() => props.widget.config || {})
```

---

_Reviewed: 2026-08-09_  
_Reviewer: gsd-code-reviewer_  
_Depth: standard_
