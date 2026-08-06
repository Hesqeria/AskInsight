<template>
  <div class="chat-page">
    <div ref="messagesEl" class="messages">
      <div
        v-for="(msg, index) in messages"
        :key="index"
        :class="['message-row', msg.role]"
      >
        <div v-if="msg.role === 'assistant'" class="avatar">🤖</div>
        <div class="bubble">
          <div v-if="msg.type === 'text'">{{ msg.content }}</div>

          <div v-else-if="msg.type === 'steps'" class="steps">
            <div v-for="(step, sIdx) in msg.steps" :key="sIdx" class="step">
              <span class="dot" :class="step.status"></span>
              <span>{{ step.text }}</span>
            </div>
          </div>

          <div v-else-if="msg.type === 'card'" class="card-result">
            <div class="card-value">{{ msg.value }}</div>
            <div class="card-label">{{ msg.label }}</div>
          </div>

          <div v-else-if="msg.type === 'chart'" class="chart-wrap">
            <div :ref="el => chartEls[index] = el" class="chart-box"></div>
          </div>

          <div v-else-if="msg.type === 'table'" class="table-wrap">
            <table class="result-table">
              <thead>
                <tr><th v-for="col in msg.columns" :key="col">{{ col }}</th></tr>
              </thead>
              <tbody>
                <tr v-for="(row, rIdx) in msg.rows" :key="rIdx">
                  <td v-for="col in msg.columns" :key="col">{{ row[col] }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-else-if="msg.type === 'reply'" class="reply-text">{{ msg.content }}</div>
          <div v-else-if="msg.type === 'error'" class="error-text">{{ msg.content }}</div>
        </div>
        <div v-if="msg.role === 'user'" class="avatar">🧑</div>
      </div>
      <div class="messages-bottom-spacer"></div>
    </div>

    <div class="input-wrapper">
      <div class="input-box">
        <input v-model="question" @keyup.enter="sendQuestion" placeholder="Ask your data question..." />
        <button @click="sendQuestion" :disabled="loading">{{ loading ? "Processing..." : "Ask" }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, ref, watch } from "vue";
import * as echarts from "echarts";

const API_URL = "/api/query";
const question = ref("");
const loading = ref(false);
const messages = ref([]);
const messagesEl = ref(null);
const chartEls = ref({});
const chartInstances = {};
const queryHistory = ref([])
const llmProviders = ref([])
const selectedProvider = ref("")

async function loadProviders(){try{var r=await fetch("/api/admin/llm/providers");var d=await r.json();if(d.providers){llmProviders.value=d.providers;selectedProvider.value=d.providers[0]||""}}catch(e){}}
async function switchLLM(){try{await fetch("/api/admin/llm/switch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({provider:selectedProvider.value})})}catch(e){}}
loadProviders()

const examples = ["Top regions by sales", "Brand sales share", "Top 3 customers by spend", "Member tier comparison", "Monthly sales trend Q1 2025", "High-value customer category"];

function cellStyle(msg,row,col){var val=row[col];if(typeof val!=="number")return"";var ck="_"+col+"_color";if(row[ck])return"color:"+row[ck];return""}

async function rateAnswer(msgIndex,rating){try{var reqId=""+Date.now();await fetch("/api/quality/rate",{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer "+(localStorage.getItem("token")||"")},body:JSON.stringify({request_id:reqId,rating:rating})});var btn=rating===1?"Rate good":"Rate feedback";alert(btn)}catch(e){alert("Rating failed")}}

 function exportCSV(msg){var cols=msg.columns;var lines=[cols.join(",")];msg.rows.forEach(function(r){lines.push(cols.map(function(c){return JSON.stringify(r[c]||"")}).join(","))});var blob=new Blob(["\uFEFF"+lines.join("\n")],{type:"text/csv;charset=utf-8"});var url=URL.createObjectURL(blob);var a=document.createElement("a");a.href=url;a.download="result.csv";a.click();URL.revokeObjectURL(url)}

function scrollToBottom() {
  const el = messagesEl.value;
  if (el) el.scrollTop = el.scrollHeight;
}

function detectType(result) {
  if (!result || !result.length) return "table";
  const keys = Object.keys(result[0]);
  if (keys.includes("reply")) return "reply";
  if (result.length === 1 && keys.length === 1) return "card";
  const numericKeys = keys.filter(k => typeof result[0][k] === "number" || /^-?\d/.test(String(result[0][k])));
  const categoryKeys = keys.filter(k => !numericKeys.includes(k));
  if (numericKeys.length >= 1 && categoryKeys.length >= 1 && result.length >= 2) return "chart";
  return "table";
}

function renderChart(index, msg) {
  nextTick(() => {
    const el = chartEls.value[index];
    if (!el) return;
    if (chartInstances[index]) chartInstances[index].dispose();
    const chart = echarts.init(el);
    chartInstances[index] = chart;
    const catKey = msg.categoryKey;
    const valKeys = msg.valueKeys;
    chart.setOption({
      tooltip: { trigger: "axis" },
      legend: { data: valKeys, top: 0 },
      grid: { left: 50, right: 20, top: 40, bottom: 60 },
      xAxis: { type: "category", data: msg.rows.map(r => String(r[catKey])), axisLabel: { rotate: 30 } },
      yAxis: { type: "value" },
      series: valKeys.map(k => ({
        name: k, type: "bar",
        data: msg.rows.map(r => Number(r[k]) || 0),
        itemStyle: { borderRadius: [4, 4, 0, 0] },
      })),
    });
  });
}

watch(messages, () => {
  messages.value.forEach((msg, idx) => {
    if (msg.type === "chart") renderChart(idx, msg);
  });
  nextTick(scrollToBottom);
}, { deep: true });

async function sendQuestion() {
  if (!question.value || loading.value) return;
  const q = question.value;
  question.value = "";
  loading.value = true;
  messages.value.push({ role: "user", type: "text", content: q });

  const stepIndex = messages.value.push({ role: "assistant", type: "steps", steps: [] }) - 1;
  await nextTick();
  scrollToBottom();

  try {
    const response = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q, history: queryHistory.value.slice(-3) }),
    });
    if (!response.body) throw new Error("Stream response failed");

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let resultData = null;
    let isError = false;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();

      for (const evt of events) {
        const line = evt.trim();
        if (!line.startsWith("data:")) continue;
        let data;
        try { data = JSON.parse(line.replace(/^data:\s*/, "")); } catch { continue; }

        const steps = messages.value[stepIndex].steps;
        if (data.stage) {
          const last = steps.at(-1);
          if (last && last.status === "running") last.status = "success";
          steps.push({ text: data.stage, status: "running" });
        } else if (data.error) {
          const last = steps.at(-1);
          if (last) last.status = "error";
          messages.value.push({ role: "assistant", type: "error", content: data.error });
          isError = true;
        } else if (Array.isArray(data.result)) {
          const last = steps.at(-1);
          if (last) last.status = "success";
          resultData = data.result;
        }
        await nextTick();
        scrollToBottom();
      }
    }

    if (resultData && !isError) {
      const t = detectType(resultData);
      if (t === "reply") {
        messages.value.push({ role: "assistant", type: "reply", content: resultData[0].reply });
      } else if (t === "card") {
        const k = Object.keys(resultData[0])[0];
        messages.value.push({ role: "assistant", type: "card", value: resultData[0][k], label: k });
      } else if (t === "chart") {
        const keys = Object.keys(resultData[0]);
        const numericKeys = keys.filter(k => typeof resultData[0][k] === "number" || /^-?\d/.test(String(resultData[0][k])));
        const categoryKeys = keys.filter(k => !numericKeys.includes(k));
        messages.value.push({
          role: "assistant", type: "chart",
          rows: resultData,
          categoryKey: categoryKeys[0] || keys[0],
          valueKeys: numericKeys,
        });
      } else {
        messages.value.push({
          role: "assistant", type: "table",
          columns: Object.keys(resultData[0] || {}),
          rows: resultData,
        });
      }
      queryHistory.value.push(q);
    }
  } catch (e) {
    messages.value.push({ role: "assistant", type: "error", content: e?.message || "Request failed" });
  } finally {
    loading.value = false;
    await nextTick();
    scrollToBottom();
  }
}
</script>

<style scoped>
:global(html), :global(body) { height: 100%; margin: 0; }
:global(body) { display: block !important; place-items: unset !important; }
:global(#app) { height: 100%; max-width: none !important; margin: 0 !important; padding: 0 !important; }
.chat-page { height: 100%; overflow: hidden; background: #fff; }
.messages { height: 100%; overflow-y: auto; padding: 20px 20% 160px; }
.message-row { display: flex; margin-bottom: 14px; }
.message-row.assistant { justify-content: flex-start; }
.message-row.user { justify-content: flex-end; }
.avatar { width: 34px; height: 34px; border-radius: 10px; background: #f3f4f6; display: flex; align-items: center; justify-content: center; margin: 0 10px; font-size: 12px; }
.bubble { max-width: min(820px, 72%); padding: 12px 14px; border-radius: 12px; background: #f5f5f5; }
.message-row.user .bubble { background: #e6f4ff; }
.steps { display: flex; flex-direction: column; gap: 6px; }
.step { display: flex; align-items: center; gap: 8px; }
.dot { width: 10px; height: 10px; border-radius: 50%; }
.dot.running { background: #f1c40f; }
.dot.success { background: #2ecc71; }
.dot.error { background: #e74c3c; }
.card-result { text-align: center; padding: 16px; }
.card-value { font-size: 32px; font-weight: 700; color: #409eff; }
.card-label { font-size: 13px; color: #999; margin-top: 4px; }
.chart-wrap { width: 100%; }
.chart-box { width: 100%; height: 320px; }
.table-wrap { max-width: 100%; overflow-x: auto; }
.result-table { width: max-content; min-width: 100%; border-collapse: collapse; }
.result-table th, .result-table td { border: 1px solid #ddd; padding: 6px 12px; white-space: nowrap; font-size: 13px; text-align: left; }
.result-table th { background: #fafafa; font-weight: 600; }
.reply-text { line-height: 1.6; }
.error-text { color: #e74c3c; font-weight: 600; }
.input-wrapper { position: fixed; left: 0; right: 0; bottom: 24px; display: flex; justify-content: center; padding: 0 16px; pointer-events: none; }
.input-box { pointer-events: auto; width: 100%; max-width: 720px; display: flex; gap: 12px; padding: 14px 16px; border-radius: 999px; background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(10px); border: 1px solid rgba(0, 0, 0, 0.08); box-shadow: 0 10px 30px rgba(0, 0, 0, 0.12); }
.input-box input { flex: 1; border: none; outline: none; background: transparent; font-size: 15px; }
.input-box button { padding: 8px 18px; border-radius: 999px; border: none; background: linear-gradient(135deg, #409eff, #66b1ff); color: #fff; cursor: pointer; }
.input-box button:disabled { opacity: 0.5; }
.export-btn{margin-top:8px;padding:4px 12px;border:1px solid #3b82f6;border-radius:4px;background:#fff;color:#3b82f6;cursor:pointer;font-size:12px}.export-btn:hover{background:#3b82f6;color:#fff}
.messages-bottom-spacer { height: 200px; }
</style>
