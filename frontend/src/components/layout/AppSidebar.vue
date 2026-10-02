<template>
  <aside class="sidebar" :class="{ open: menuOpen }">
    <div class="sidebar-header"><span class="logo">{{ t('title') }}</span></div>
    <nav class="nav-links">
      <router-link to="/" class="nav-item">{{ t('chat') }}</router-link>
      <router-link to="/dashboard" class="nav-item">Dashboard</router-link>
      <router-link to="/schema" class="nav-item">{{ t('schema') }}</router-link>
      <router-link to="/settings" class="nav-item">{{ t('settings') }}</router-link>
      <router-link to="/admin/pipelines" class="nav-item">{{ t('pipelines') }}</router-link>
      <router-link to="/admin/readiness" class="nav-item">{{ t('readiness') }}</router-link>
    </nav>

    <div class="sidebar-section" v-if="inbox.pendingCount > 0">
      <h4>
        <span class="amber-dot"></span>{{ t('pendingTasks') }}
        <span class="pending-badge">{{ inbox.pendingCount }}</span>
      </h4>
      <div v-for="it in inbox.items" :key="it.inbox_id" class="hist-item pending-item">
        <span class="amber-dot small"></span>
        <span class="kind-label">{{ kindLabel(it.kind) }}</span>
        <span class="item-time">{{ shortTime(it.created_at) }}</span>
      </div>
    </div>

    <div class="sidebar-section" v-if="history.length">
      <h4>{{ t('history') }}</h4>
      <div v-for="(h, i) in history.slice(0, 20)" :key="i" class="hist-item" @click="emit('replay', h)">
        {{ h.slice(0, 40) }}{{ h.length > 40 ? '...' : '' }}
      </div>
    </div>

    <div class="sidebar-section" v-if="favorites.length">
      <h4>{{ t('favorites') }}</h4>
      <div v-for="(f, i) in favorites" :key="i" class="hist-item" @click="emit('replay', f)">
        {{ f.slice(0, 40) }}{{ f.length > 40 ? '...' : '' }}
      </div>
    </div>
  </aside>
</template>

<script setup>
import { useI18n } from '../../utils/i18n.js'
import { useInboxStore } from '../../stores/inbox.js'

const inbox = useInboxStore()

const KIND_LABELS = { clarify: '澄清', approval: '审批' }
function kindLabel(kind) { return KIND_LABELS[kind] || kind }
function shortTime(ts) {
  if (!ts) return ''
  return String(ts).replace('T', ' ').slice(5, 16)
}

defineProps({
  history: { type: Array, default: () => [] },
  favorites: { type: Array, default: () => [] },
  menuOpen: { type: Boolean, default: false },
})

const emit = defineEmits(['replay'])
const { t } = useI18n()
</script>

<style scoped>
.amber-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--color-accent, #C9822E); margin-right: 6px; animation: pulse 2s infinite; box-shadow: 0 0 6px rgba(201, 130, 46, 0.6); }
.amber-dot.small { width: 6px; height: 6px; margin-right: 6px; flex-shrink: 0; }
.pending-badge { background: var(--color-accent, #C9822E); color: #1A1208; border-radius: 999px; font-size: 10px; font-weight: 700; padding: 0 6px; margin-left: 6px; }
.pending-item { display: flex; align-items: center; gap: 4px; }
.kind-label { flex: 1; }
.item-time { font-size: 10px; opacity: 0.6; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
</style>
