<template>
  <div class="page">
    <h2>Feedback</h2>
    <p class="desc">User corrections and quality ratings recorded by the feedback loop. Corrected SQL is reused as reference examples for future queries.</p>

    <div class="card stats" v-if="stats">
      <div class="stat"><span class="num">{{ stats.total }}</span><span class="lbl">Total</span></div>
      <div class="stat good"><span class="num">{{ stats.good }}</span><span class="lbl">Good</span></div>
      <div class="stat bad"><span class="num">{{ stats.bad }}</span><span class="lbl">Bad</span></div>
    </div>

    <div class="card">
      <h3>Submit a correction</h3>
      <textarea v-model="form.query" placeholder="Original question" rows="2"></textarea>
      <textarea v-model="form.wrong_sql" placeholder="Wrong SQL (optional)" rows="2"></textarea>
      <textarea v-model="form.corrected_sql" placeholder="Corrected SQL (must be SELECT)" rows="3"></textarea>
      <button class="primary" @click="submit" :disabled="submitting || !form.query || !form.corrected_sql">
        {{ submitting ? 'Submitting...' : 'Submit Correction' }}
      </button>
      <p v-if="submitMsg" class="msg" :class="{ error: submitError }">{{ submitMsg }}</p>
    </div>
  </div>
</template>

<script>
import { apiGet, apiPost } from '../../utils/api.js'
export default {
  data() {
    return {
      stats: null, submitting: false, submitMsg: '', submitError: false,
      form: { query: '', wrong_sql: '', corrected_sql: '' },
    };
  },
  async mounted() {
    try {
      this.stats = await apiGet('/api/quality/stats');
    } catch (e) { /* stats optional */ }
  },
  methods: {
    async submit() {
      this.submitting = true; this.submitMsg = ''; this.submitError = false;
      try {
          const d = await apiPost('/api/feedback', this.form);
        
        this.submitError = d.status !== 'ok';
        this.submitMsg = d.message || d.status;
        if (d.status === 'ok') this.form = { query: '', wrong_sql: '', corrected_sql: '' };
      } catch (e) { this.submitError = true; this.submitMsg = 'Request failed: ' + e.message; }
      finally { this.submitting = false; }
    },
  },
};
</script>

<style scoped>
.page { max-width: 800px; margin: 0 auto; padding: 20px; }
.desc { color: #555; }
.card { background: #fff; border: 1px solid #eee; border-radius: 8px; padding: 16px; margin: 12px 0; }
.card h3 { margin: 0 0 12px; font-size: 15px; }
.stats { display: flex; gap: 20px; }
.stat { text-align: center; flex: 1; }
.num { display: block; font-size: 28px; font-weight: 700; }
.stat.good .num { color: #52c41a; }
.stat.bad .num { color: #ff4d4f; }
.lbl { color: #888; font-size: 12px; }
textarea { width: 100%; box-sizing: border-box; margin-bottom: 10px; padding: 8px; border: 1px solid #ddd; border-radius: 6px; font-family: monospace; font-size: 13px; }
button.primary { padding: 8px 20px; border: none; border-radius: 6px; background: #1890ff; color: #fff; cursor: pointer; }
button.primary:disabled { opacity: 0.5; }
.msg { margin-top: 10px; color: #1890ff; font-size: 13px; }
.msg.error { color: #e74c3c; }
</style>
