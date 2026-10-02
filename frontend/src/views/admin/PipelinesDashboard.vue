<template>
  <div class="pipes">
    <h2>Pipeline Engine</h2>
    <div class="cards">
      <div v-for="t in templates" :key="t.name" class="card" @click="run(t.name)">
        <h3>{{ t.name }}</h3>
        <p>{{ t.description }}</p>
        <button :disabled="running===t.name">{{ running===t.name?'Running...':'Run' }}</button>
      </div>
    </div>
    <div v-if="progress.length" class="panel">
      <h3>{{ activeName }} <span :class="'tag '+runState">{{ runState }}</span></h3>
      <div class="steps">
        <div v-for="(s,i) in progress" :key="i" :class="'step '+s.state">
          <span class="dot"></span>{{ s.node }} {{ s.progress }}%
        </div>
      </div>
      <button v-if="runState==='FAILED'" @click="resumeRun">Resume</button>
    </div>
    <div class="history">
      <h3>History</h3>
      <div v-for="r in runs" :key="r.run_id" class="row">
        <span :class="'st '+r.state">{{ r.state }}</span>
        <span>{{ r.pipeline_name }}</span>
        <span class="ts">{{ r.started_at }}</span>
      </div>
    </div>
  </div>
</template>
<script>
import { apiGet, apiPost } from '../../utils/api.js'
export default {
  data(){return{templates:[],runs:[],running:null,progress:[],runState:'',activeName:'',activeRunId:null}},
  async mounted(){await this.load()},
  methods:{
    async load(){
      const d=await apiGet('/api/pipelines')
      this.templates=d.templates||[];this.runs=d.runs||[]
    },
    async run(name){
      this.running=name;this.activeName=name;this.progress=[]
      const d=await apiPost('/api/pipelines/'+name+'/run', {query:''})
      this.runState=d.state;this.activeRunId=d.run_id
      if(d.outputs) this.progress=Object.keys(d.outputs).map(k=>({node:k,state:'DONE',progress:100}))
      this.running=null
    },
    async resumeRun(){
      if(!this.activeRunId)return
      const d=await apiPost('/api/pipelines/runs/'+this.activeRunId+'/resume', {})
      this.runState=d.state
      if(d.outputs) this.progress=Object.keys(d.outputs).map(k=>({node:k,state:'DONE',progress:100}))
    }
  }
}
</script>
<style scoped>
.pipes{max-width:800px;margin:0 auto;padding:20px}
.cards{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px}
.card{flex:1;min-width:220px;padding:16px;border-radius:8px;border:1px solid #e0e0e0;cursor:pointer}
.card:hover{box-shadow:0 4px 12px rgba(0,0,0,.1)}
.card h3{margin:0 0 6px;font-size:16px}
.card p{font-size:13px;color:#888;margin:0 0 10px}
.card button{padding:6px 16px;border:none;border-radius:4px;background:#1890ff;color:#fff;cursor:pointer}
.panel{background:#f9f9f9;padding:16px;border-radius:8px;margin-bottom:20px}
.panel h3{margin:0 0 10px}
.tag{padding:1px 8px;border-radius:3px;font-size:11px;margin-left:8px}
.tag.DONE{background:#52c41a;color:#fff}.tag.FAILED{background:#ff4d4f;color:#fff}
.steps{display:flex;gap:4px;flex-wrap:wrap}
.step{padding:5px 10px;border-radius:4px;font-size:12px;display:flex;align-items:center;gap:4px}
.step.DONE{background:#e6ffe6;color:#389e0d}.step.FAILED{background:#fff2f0;color:#cf1322}
.dot{width:8px;height:8px;border-radius:50%;background:currentColor}
.row{display:flex;gap:12px;padding:8px 12px;margin:3px 0;border-radius:4px;font-size:13px;align-items:center}
.row:hover{background:#f5f5f5}
.st{font-weight:700;font-size:11px}.st.DONE{color:#52c41a}.st.FAILED{color:#ff4d4f}
.ts{color:#aaa;font-size:11px;margin-left:auto}
</style>
