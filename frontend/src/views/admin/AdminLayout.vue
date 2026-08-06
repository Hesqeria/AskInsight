<template>
  <div class="admin-layout">
    <aside class="sidebar">
      <div class="logo">AskInsight Admin</div>
      <nav>
        <router-link to="/admin/meta" class="nav-item">Metadata</router-link>
        <router-link to="/admin/glossary" class="nav-item">Glossary</router-link>
        <router-link to="/admin/datasource" class="nav-item">Data Sources</router-link>
        <router-link to="/admin/prompts" class="nav-item">Prompts</router-link>
        <router-link to="/admin/feedback" class="nav-item">Feedback</router-link>
        <router-link to="/admin/audit" class="nav-item">Audit Log</router-link>
        <router-link to="/" class="nav-item back">Home</router-link>
      </nav>
    </aside>
    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
</script>

<style scoped>
.admin-layout { display: flex; height: 100vh; overflow: hidden; }
.sidebar { width: 220px; background: #1e293b; color: #e2e8f0; padding: 0; flex-shrink: 0; overflow-y: auto; }
.logo { padding: 20px 16px; font-size: 15px; font-weight: 700; border-bottom: 1px solid #334155; }
.nav-item { display: block; padding: 12px 16px; color: #cbd5e1; text-decoration: none; font-size: 14px; transition: all 0.2s; border-left: 3px solid transparent; }
.nav-item:hover { background: #334155; color: #fff; }
.nav-item.router-link-active { background: #0ea5e920; border-left-color: #0ea5e9; color: #38bdf8; }
.nav-item.back { margin-top: 20px; border-top: 1px solid #334155; }
.content { flex: 1; overflow-y: auto; padding: 24px; background: #f1f5f9; }
</style>
