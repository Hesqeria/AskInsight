<template>
  <div class="app-shell">
    <AppSidebar
      :history="queryHistory"
      :favorites="favQueries"
      :menu-open="menuOpen"
      @replay="onHistoryReplay"
    />
    <router-view @replay="onHistoryReplay" />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import AppSidebar from './components/layout/AppSidebar.vue'
import { useHistory } from './composables/useHistory.js'

const { queryHistory, favQueries } = useHistory()
const menuOpen = ref(false)

function onHistoryReplay(q) {
  menuOpen.value = false
}
</script>

<style>
@import './styles/variables.css';
@import './styles/chat.css';
</style>
