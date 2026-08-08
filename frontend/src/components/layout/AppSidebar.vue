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

defineProps({
  history: { type: Array, default: () => [] },
  favorites: { type: Array, default: () => [] },
  menuOpen: { type: Boolean, default: false },
})

const emit = defineEmits(['replay'])
const { t } = useI18n()
</script>
