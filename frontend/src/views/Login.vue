<template>
  <div class="login-stage">
    <div class="login-aura" aria-hidden="true"></div>
    <form class="login-card" @submit.prevent="doLogin">
      <div class="brand">
        <span class="brand-mark">问</span>
        <div class="brand-text">
          <h2>AskInsight</h2>
          <span class="brand-sub">智能问数 · 数据工作台</span>
        </div>
      </div>
      <label class="field">
        <span class="field-label">用户名</span>
        <input v-model="username" placeholder="admin" autocomplete="username" />
      </label>
      <label class="field">
        <span class="field-label">密码</span>
        <input v-model="password" type="password" placeholder="••••••••" autocomplete="current-password" />
      </label>
      <p v-if="error" class="err">{{ error }}</p>
      <button type="submit" class="submit" :disabled="loading">
        {{ loading ? '登录中…' : '进入工作台' }}
      </button>
      <p class="hint">默认账号 admin / change-me(JWT 24h,过期自动回到本页)</p>
    </form>
  </div>
</template>

<script>
export default {
  name: 'LoginView',
  data() { return { username: '', password: '', loading: false, error: '' } },
  methods: {
    async doLogin() {
      this.loading = true; this.error = ''
      try {
        const r = await fetch('/api/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: this.username, password: this.password })
        })
        if (!r.ok) {
          this.error = r.status === 401 ? '用户名或密码错误'
            : r.status === 429 ? '尝试过于频繁,请稍后再试' : `HTTP ${r.status}`
          return
        }
        const d = await r.json()
        localStorage.setItem('token', d.token)
        this.$router.push(this.$route.query.redirect || '/')
      } catch (e) {
        this.error = '网络错误: ' + e
      } finally { this.loading = false }
    }
  }
}
</script>

<style scoped>
.login-stage {
  position: relative;
  display: flex; justify-content: center; align-items: center;
  min-height: 100vh;
  background: linear-gradient(160deg, #0F1915 0%, #16241F 55%, #0E1B17 100%);
  overflow: hidden;
}
.login-aura {
  position: absolute; inset: 0; pointer-events: none;
  background:
    radial-gradient(600px 420px at 18% 22%, rgba(46, 155, 135, 0.22), transparent 65%),
    radial-gradient(520px 380px at 82% 78%, rgba(201, 130, 46, 0.12), transparent 60%),
    radial-gradient(300px 300px at 78% 18%, rgba(98, 188, 172, 0.10), transparent 70%);
}
.login-aura::after {
  content: "";
  position: absolute; inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.025) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: radial-gradient(720px 540px at 50% 46%, #000 30%, transparent 75%);
}
.login-card {
  position: relative;
  width: 380px; padding: 36px 32px 28px;
  border-radius: 18px;
  background: rgba(21, 33, 28, 0.82);
  border: 1px solid rgba(255, 255, 255, 0.08);
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(10px);
  display: flex; flex-direction: column; gap: 16px;
  animation: rise-in 0.4s cubic-bezier(0.4, 0, 0.2, 1) both;
}
.brand { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
.brand-mark {
  width: 44px; height: 44px; border-radius: 13px;
  background: linear-gradient(135deg, #2E9B87, #0E7C6B);
  color: #F2FBF8; font-size: 21px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 4px 14px rgba(14, 124, 107, 0.5);
}
.brand-text h2 { margin: 0; font-size: 19px; letter-spacing: 0.02em; color: #EFF6F3; }
.brand-sub { font-size: 12px; color: #8FA39B; letter-spacing: 0.06em; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field-label { font-size: 12px; color: #8FA39B; letter-spacing: 0.05em; }
.field input {
  padding: 11px 13px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.05);
  color: #E9F0ED; font-size: 14px;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.field input::placeholder { color: #63756D; }
.field input:focus {
  outline: none;
  border-color: #3FA796;
  box-shadow: 0 0 0 3px rgba(63, 167, 150, 0.22);
}
.submit {
  margin-top: 4px;
  padding: 12px; border: none; border-radius: 10px;
  background: linear-gradient(135deg, #2E9B87, #0E7C6B);
  color: #fff; font-size: 14px; font-weight: 600; letter-spacing: 0.04em;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(14, 124, 107, 0.45);
  transition: transform 0.2s, box-shadow 0.2s, opacity 0.2s;
}
.submit:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 6px 20px rgba(14, 124, 107, 0.55); }
.submit:disabled { opacity: 0.55; cursor: not-allowed; }
.err { color: #E58A7E; font-size: 13px; margin: 0; }
.hint { color: #6B7E76; font-size: 12px; margin: 2px 0 0; text-align: center; }
</style>
