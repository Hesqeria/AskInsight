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
import { ref, onMounted, onUnmounted } from 'vue'
import AppSidebar from './components/layout/AppSidebar.vue'
import { useHistory } from './composables/useHistory.js'
import { useEchartsTheme } from './composables/useEchartsTheme.js'

const { queryHistory, favQueries } = useHistory()
const { watchTheme } = useEchartsTheme()
const menuOpen = ref(false)
let stopWatchTheme = null

onMounted(() => { stopWatchTheme = watchTheme() })
onUnmounted(() => stopWatchTheme?.())

function onHistoryReplay(q) {
  menuOpen.value = false
}
</script>

<style>
@import './styles/variables.css';
@import './styles/chat.css';
</style>
