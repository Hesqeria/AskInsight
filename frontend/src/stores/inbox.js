/**
 * Unified pending-interaction store (PRD M3).
 *
 * One concept for every async interaction kind (clarify / approval /
 * future ones): the sidebar's amber status dots and the chat's pending
 * cards all read from here. Polling /api/v1/inbox/pending; starts
 * lazily on first use and stops when the tab hides (visibilitychange).
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

function headers() {
  const token = localStorage.getItem('token') || ''
  return token ? { Authorization: 'Bearer ' + token } : {}
}

/** Best-effort role extraction from the JWT (approver gating in UI). */
export function userRole() {
  try {
    const token = localStorage.getItem('token') || ''
    const payload = token.split('.')[1]
    if (!payload) return ''
    const b64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    const json = JSON.parse(atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4)))
    return json.role || json.authorities?.[0] || ''
  } catch { return '' }
}

export const APPROVER_ROLES = ['L3_engineer', 'L4_admin', 'admin']

export const useInboxStore = defineStore('inbox', () => {
  const items = ref([])
  const loaded = ref(false)
  const polling = ref(false)
  let timer = null

  const pendingCount = computed(() => items.value.filter(i => i.status === 'pending').length)
  const hasPending = computed(() => pendingCount.value > 0)
  const canApprove = computed(() => APPROVER_ROLES.includes(userRole()))

  async function refresh() {
    try {
      const resp = await fetch('/api/v1/inbox/pending?limit=50', { headers: headers() })
      if (!resp.ok) return
      const body = await resp.json()
      items.value = body.items || []
      loaded.value = true
    } catch { /* offline: keep last snapshot */ }
  }

  function startPolling(intervalMs = 15000) {
    if (timer) return
    polling.value = true
    refresh()
    timer = setInterval(refresh, intervalMs)
    document.addEventListener('visibilitychange', onVisibility)
  }

  function stopPolling() {
    if (timer) { clearInterval(timer); timer = null }
    polling.value = false
    document.removeEventListener('visibilitychange', onVisibility)
  }

  function onVisibility() {
    if (document.visibilityState === 'visible') refresh()
  }

  function removeById(inboxId) {
    items.value = items.value.filter(i => i.inbox_id !== inboxId)
  }

  return {
    items, loaded, polling, pendingCount, hasPending, canApprove,
    refresh, startPolling, stopPolling, removeById,
  }
})
