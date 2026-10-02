<template>
  <div class="gov">
    <h2>数据治理大盘 (GOV)</h2>
    <div class="actions">
      <button @click="loadAll" :disabled="loading">{{ loading ? 'Loading...' : 'Refresh' }}</button>
      <button @click="runQuality" :disabled="qualityLoading">{{ qualityLoading ? 'Running...' : 'Run Quality Checks' }}</button>
      <span v-if="qualityMsg" class="qmsg">{{ qualityMsg }}</span>
    </div>

    <section v-if="overview" class="cards">
      <div class="card">
        <h4>资产 (GOV-1)</h4>
        <div class="kv"><span>已登记</span><b class="ok">{{ overview.assets.registered }}</b></div>
        <div class="kv"><span>未登记</span><b class="warn">{{ overview.assets.unregistered }}</b></div>
        <div class="kv"><span>YAML 缺失</span><b>{{ overview.assets.stale }}</b></div>
        <div class="kv"><span>覆盖率</span><b>{{ overview.assets.coverage_pct }}%</b></div>
      </div>
      <div class="card">
        <h4>落标 (GOV-2)</h4>
        <div class="lights">
          <span class="dot green"></span>{{ overview.compliance.traffic_light.green }}
          <span class="dot yellow"></span>{{ overview.compliance.traffic_light.yellow }}
          <span class="dot red"></span>{{ overview.compliance.traffic_light.red }}
        </div>
        <div class="kv"><span>表注释率</span><b>{{ overview.compliance.rates.table_comment_pct }}%</b></div>
        <div class="kv"><span>字段注释率</span><b>{{ overview.compliance.rates.column_comment_pct }}%</b></div>
        <div class="kv"><span>YAML 口径率</span><b>{{ overview.compliance.rates.yaml_desc_pct }}%</b></div>
      </div>
      <div class="card">
        <h4>质量 (GOV-3)</h4>
        <template v-if="overview.quality.last_run">
          <div class="kv"><span>最近运行</span><b>{{ overview.quality.last_run.checked_at }}</b></div>
          <div class="kv"><span>检查/通过</span><b>{{ overview.quality.last_run.checks_passed }}/{{ overview.quality.last_run.checks_total }}</b></div>
        </template>
        <div class="kv"><span>未修复失败</span><b class="bad">{{ overview.quality.open_failures }}</b></div>
      </div>
      <div class="card">
        <h4>成本 (GOV-4)</h4>
        <div class="kv"><span>状态</span><b>{{ overview.cost.status }}</b></div>
        <p class="note">{{ overview.cost.note }}</p>
      </div>
    </section>

    <section v-if="inventory">
      <h3>资产清单差异 ({{ inventory.dw_table_count }} 表 vs YAML {{ inventory.yaml_table_count }})</h3>
      <table>
        <thead><tr><th>表</th><th>分类</th><th>注释</th><th>行数(估)</th></tr></thead>
        <tbody>
          <tr v-for="t in inventory.unregistered.tables" :key="t.table">
            <td>{{ t.table }}</td><td><span class="tag" :class="t.class">{{ t.class }}</span></td>
            <td>{{ t.comment }}</td><td>{{ t.rows_est }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="compliance">
      <h3>落标红绿灯</h3>
      <table>
        <thead><tr><th>表</th><th>灯</th><th>表注释</th><th>字段注释%</th><th>YAML口径%</th></tr></thead>
        <tbody>
          <tr v-for="t in redYellow" :key="t.table">
            <td>{{ t.table }}</td>
            <td><span class="dot" :class="t.light"></span></td>
            <td>{{ t.table_comment ? 'Y' : '-' }}</td>
            <td>{{ t.column_comment_pct }}</td>
            <td>{{ t.yaml_desc_pct ?? '-' }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="failures.length">
      <h3>质量失败 ({{ failures.length }})</h3>
      <table>
        <thead><tr><th>表</th><th>规则</th><th>值</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="(f, i) in failures" :key="i">
            <td>{{ f.table_name }}</td><td>{{ f.rule_type }}</td>
            <td>{{ f.metric_value }}</td><td>{{ f.detail }}</td>
          </tr>
        </tbody>
      </table>
      <p class="note">治理原则:失败仅记录+建议,修复由人拍板(不自动改数)。</p>
    </section>

    <section>
      <h3>治理任务编排</h3>
      <table>
        <thead><tr><th>任务</th><th>类型</th><th>间隔</th><th>状态</th><th>最近运行</th><th>结果</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.task_id">
            <td>{{ t.task_id }}</td>
            <td>{{ t.task_type }}</td>
            <td>{{ t.interval_minutes }}min</td>
            <td>
              <span class="tag" :class="t.enabled ? 'on' : 'off'">{{ t.enabled ? '启用' : '停用' }}</span>
              <span v-if="t.enabled && t.due" class="tag due">待执行</span>
            </td>
            <td>{{ t.last_run_at || '-' }}</td>
            <td>
              <template v-if="t.last_status">
                <span class="tag" :class="t.last_status === 'success' ? 'on' : 'off'">{{ t.last_status }}</span>
                <small v-if="detailOf(t)">{{ detailOf(t) }}</small>
              </template>
              <template v-else>-</template>
            </td>
            <td>
              <button class="mini" :disabled="taskBusy[t.task_id]" @click="runTask(t.task_id)">Run</button>
              <button class="mini" :disabled="taskBusy[t.task_id]" @click="toggleTask(t.task_id)">{{ t.enabled ? '停用' : '启用' }}</button>
            </td>
          </tr>
          <tr v-if="!tasks.length"><td colspan="7" class="note">加载中或无任务</td></tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script>
import { apiGet, apiPost } from '../../utils/api.js'

export default {
  name: 'GovDashboard',
  data() {
    return {
      loading: false, qualityLoading: false, qualityMsg: '',
      overview: null, inventory: null, compliance: null, failures: [],
      tasks: [], taskBusy: {}
    }
  },
  computed: {
    redYellow() {
      if (!this.compliance) return []
      return this.compliance.tables.filter(t => t.light !== 'green')
    }
  },
  async mounted() { await this.loadAll() },
  methods: {
    async loadAll() {
      this.loading = true
      try {
        const [ov, inv, comp] = await Promise.all([
          apiGet('/api/admin/gov/overview'),
          apiGet('/api/admin/gov/inventory'),
          apiGet('/api/admin/gov/compliance')
        ])
        this.overview = ov; this.inventory = inv; this.compliance = comp
        if (ov && ov.quality && ov.quality.last_run) await this.loadFailures()
        await this.loadTasks()
      } catch (e) { console.error(e) } finally { this.loading = false }
    },
    async loadFailures() {
      try {
        const r = await apiGet('/api/admin/gov/quality/latest-failures')
        this.failures = r.failures || []
      } catch (e) { this.failures = [] }
    },
    async loadTasks() {
      try {
        const r = await apiGet('/api/admin/gov/tasks')
        this.tasks = r.tasks || []
      } catch (e) { this.tasks = [] }
    },
    detailOf(t) {
      if (!t.last_detail) return ''
      try {
        const d = typeof t.last_detail === 'string' ? JSON.parse(t.last_detail.replace(/'/g, '"')) : t.last_detail
        return d.checks_total != null ? d.checks_passed + '/' + d.checks_total + ' 过' : ''
      } catch (e) { return '' }
    },
    async runTask(id) {
      this.taskBusy = { ...this.taskBusy, [id]: true }
      try {
        await apiPost('/api/admin/gov/tasks/' + id + '/run')
        await this.loadTasks()
        this.overview = await apiGet('/api/admin/gov/overview')
        if (this.overview && this.overview.quality && this.overview.quality.last_run) await this.loadFailures()
      } catch (e) { console.error(e) } finally { this.taskBusy = { ...this.taskBusy, [id]: false } }
    },
    async toggleTask(id) {
      this.taskBusy = { ...this.taskBusy, [id]: true }
      try {
        await apiPost('/api/admin/gov/tasks/' + id + '/toggle')
        await this.loadTasks()
      } catch (e) { console.error(e) } finally { this.taskBusy = { ...this.taskBusy, [id]: false } }
    },
    async runQuality() {
      this.qualityLoading = true; this.qualityMsg = ''
      try {
        const r = await apiPost('/api/admin/gov/quality/run', {})
        this.qualityMsg = `run ${r.run_id}: ${r.checks_passed}/${r.checks_total} passed`
        this.failures = r.failures || []
        this.overview = await apiGet('/api/admin/gov/overview')
      } catch (e) {
        this.qualityMsg = 'failed: ' + (e.message || e)
      } finally { this.qualityLoading = false }
    }
  }
}
</script>

<style scoped>
.gov { max-width: 1000px; margin: 0 auto; padding: 20px; }
.actions { margin: 12px 0; display: flex; gap: 10px; align-items: center; }
.qmsg { color: #64748b; font-size: 13px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin: 16px 0; }
.card { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; }
.card h4 { margin: 0 0 10px; font-size: 14px; color: #475569; }
.kv { display: flex; justify-content: space-between; font-size: 13px; padding: 3px 0; }
.kv span { color: #94a3b8; }
.ok { color: #16a34a; } .warn { color: #d97706; } .bad { color: #dc2626; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin: 0 4px 0 10px; }
.dot.green { background: #16a34a; } .dot.yellow { background: #d97706; } .dot.red { background: #dc2626; }
.lights { font-size: 14px; margin-bottom: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #e2e8f0; }
th { color: #64748b; font-weight: 600; }
.tag { padding: 1px 8px; border-radius: 4px; font-size: 12px; background: #f1f5f9; }
.tag.test_artifact { background: #fee2e2; } .tag.suspected_rename { background: #fef3c7; }
.note { color: #94a3b8; font-size: 12px; }
.tag.on { background: #dcfce7; color: #15803d; }
.tag.off { background: #f1f5f9; color: #64748b; }
.tag.due { background: #fef3c7; color: #b45309; margin-left: 4px; }
button.mini { padding: 2px 10px; margin-right: 6px; font-size: 12px; border: 1px solid #cbd5e1; border-radius: 6px; background: #fff; cursor: pointer; }
button.mini:hover { background: #f8fafc; }
button.mini:disabled { opacity: 0.5; cursor: default; }
td small { color: #94a3b8; margin-left: 4px; }
section { margin: 24px 0; }
h3 { font-size: 15px; color: #334155; }
</style>
