<template>
  <div class="readiness">
    <h2>System Readiness</h2>
    <div class="overall" :class="scoreColor">
      <span class="big-score">{{ score }}</span><span class="unit">/100</span>
      <span class="tag">{{ passed ? 'ENABLED' : 'LOCKED' }}</span>
    </div>
    <div class="dims">
      <div v-for="d in dims" :key="d.name" class="dim-row" :class="d.passed?'pass':'fail'">
        <span class="dim-name">{{ d.name }}</span>
        <div class="dim-bar"><div class="dim-fill" :style="{width:d.score+'%'}"></div></div>
        <span class="dim-score">{{ d.score }}%</span>
        <span class="dim-tag">{{ d.passed ? 'OK' : 'FIX' }}</span>
      </div>
    </div>
    <div class="btns">
      <button @click="refresh" :disabled="loading">{{ loading?'Checking...':'Re-check' }}</button>
      <button @click="smoke" :disabled="smokeLoading">{{ smokeLoading?'Running...':'Smoke Test' }}</button>
    </div>
    <div v-if="smokeResults.length" class="smoke">
      <h4>Agent Smoke Test</h4>
      <div v-for="t in smokeResults" :key="t.name" :class="t.passed?'pass':'fail'">
        {{ t.passed?'OK':'FAIL' }} {{ t.name }} ({{ t.elapsed_ms }}ms)
      </div>
    </div>
  </div>
</template>
<script>
import { apiGet, apiPost } from '../../utils/api.js'
export default {
  data(){return{score:0,passed:false,dims:[],loading:false,smokeLoading:false,smokeResults:[]}},
  computed:{scoreColor(){return this.score>=90?'great':this.score>=70?'ok':'bad'}},
  async mounted(){await this.refresh()},
  methods:{
    async refresh(){
      this.loading=true;
      try{
        const d=await apiGet('/api/readiness');
        this.score=d.total_score;this.passed=d.passed;this.dims=d.dimensions||[];
      }catch(e){console.error(e)}finally{this.loading=false}
    },
    async smoke(){
      this.smokeLoading=true;
      try{
        const d=await apiPost('/api/readiness/agent', {});
        this.smokeResults=d.tests||[];
      }catch(e){console.error(e)}finally{this.smokeLoading=false}
    }
  }
}
</script>
<style scoped>
.readiness{max-width:800px;margin:0 auto;padding:20px}
.overall{text-align:center;padding:24px;border-radius:12px;margin-bottom:16px}
.overall.great{background:#e6ffe6}.overall.ok{background:#fff7e6}.overall.bad{background:#ffe6e6}
.big-score{font-size:48px;font-weight:700}
.unit{font-size:20px;color:#999}
.tag{margin-left:12px;padding:2px 12px;border-radius:4px;font-size:14px;background:#1890ff;color:#fff}
.dims{margin:16px 0}
.dim-row{display:flex;align-items:center;gap:10px;padding:10px;margin:4px 0;border-radius:6px}
.dim-row.pass{border-left:3px solid #52c41a;background:#f6ffed}
.dim-row.fail{border-left:3px solid #ff4d4f;background:#fff2f0}
.dim-name{width:160px;font-size:13px}
.dim-bar{flex:1;height:8px;background:#f0f0f0;border-radius:4px}
.dim-fill{height:100%;border-radius:4px;background:#52c41a}
.dim-row.fail .dim-fill{background:#ff4d4f}
.dim-score{width:40px;text-align:right;font-size:13px}
.dim-tag{padding:1px 6px;border-radius:3px;font-size:11px;font-weight:700}
.dim-row.pass .dim-tag{background:#52c41a;color:#fff}
.dim-row.fail .dim-tag{background:#ff4d4f;color:#fff}
.btns{margin:20px 0;display:flex;gap:10px}
.btns button{padding:8px 20px;border:none;border-radius:6px;cursor:pointer}
.btns button:first-child{background:#1890ff;color:#fff}
.btns button:last-child{background:#f0f0f0}
.smoke{margin-top:16px}.smoke h4{margin-bottom:8px}
.smoke>div{padding:6px 10px;margin:2px 0;border-radius:4px;font-size:13px}
.smoke>.pass{background:#f6ffed}.smoke>.fail{background:#fff2f0}
</style>
