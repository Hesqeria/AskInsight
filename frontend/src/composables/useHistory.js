import { ref, watch } from 'vue'

const HISTORY_KEY = 'askinsight_history'
const FAVS_KEY = 'askinsight_favs'
const MAX_HISTORY = 50

const queryHistory = ref(JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'))
const favQueries = ref(JSON.parse(localStorage.getItem(FAVS_KEY) || '[]'))

watch(queryHistory, (v) => localStorage.setItem(HISTORY_KEY, JSON.stringify(v)), { deep: true })
watch(favQueries, (v) => localStorage.setItem(FAVS_KEY, JSON.stringify(v)), { deep: true })

export function useHistory() {
  function addQuery(q) {
    queryHistory.value.unshift(q)
    if (queryHistory.value.length > MAX_HISTORY) queryHistory.value = queryHistory.value.slice(0, MAX_HISTORY)
  }

  function toggleFavorite(q) {
    const idx = favQueries.value.indexOf(q)
    if (idx >= 0) favQueries.value.splice(idx, 1)
    else favQueries.value.unshift(q)
  }

  function isFavorite(q) {
    return favQueries.value.includes(q)
  }

  function searchHistory(term) {
    const lower = term.toLowerCase()
    return queryHistory.value.filter(h => h.toLowerCase().includes(lower)).slice(0, 5)
  }

  return { queryHistory, favQueries, addQuery, toggleFavorite, isFavorite, searchHistory }
}
