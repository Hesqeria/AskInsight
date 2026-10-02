import { defineStore } from 'pinia'
import { ref } from 'vue'

const API_BASE = '/api/v1/clarify'

function getToken() {
  return localStorage.getItem('token') || ''
}

export const useClarifyStore = defineStore('clarify', () => {
  // Active clarification card (populated from SSE `clarify_required` event).
  const activeCard = ref(null)
  // Current user selections per field (measure/time/group_by).
  const selections = ref({})
  // Whether the confirm/resume request is in flight.
  const submitting = ref(false)
  // Latest error message (if any).
  const error = ref('')
  // Confirmed-but-not-yet-sent response payload. The confirm flow no
  // longer POSTs here; the view streams the M3 inbox respond endpoint
  // (one request, one SSE stream) and reads this payload.
  const pendingResponse = ref(null)

  function setActiveCard(card) {
    activeCard.value = card
    selections.value = {}
    error.value = ''
  }

  function clearActiveCard() {
    activeCard.value = null
    selections.value = {}
    submitting.value = false
    error.value = ''
  }

  function takePendingResponse() {
    const p = pendingResponse.value
    pendingResponse.value = null
    return p
  }

  function select(field, value) {
    // Preserve prior selections; replace per-field value.
    selections.value = { ...selections.value, [field]: value }
  }

  function setGroupBy(classes) {
    selections.value = { ...selections.value, group_by: classes }
  }

  /**
   * POST the user's response to /resume.
   * @param {object} opts - { responseType, freeText, confirmed }
   * @returns {Promise<{status:string, mergedPlan?:object, suggestions?:Array}>}
   *   When status === 'amended', the backend returns a new card.
   */
  async function submitResponse({ responseType = 'selection', freeText = '', confirmed = true } = {}) {
    const card = activeCard.value
    if (!card || !card.clarify_id) throw new Error('No active clarify session')
    error.value = ''
    // Confirmed path: stage the payload; the view drives the single
    // streamed resume via the inbox respond endpoint (no double POST).
    if (confirmed && responseType !== 'cancel') {
      pendingResponse.value = {
        response_type: responseType,
        selections: { ...selections.value },
        free_text: freeText || '',
        confirmed: true,
      }
      clearActiveCard()
      return { status: 'confirmed' }
    }
    submitting.value = true
    const token = getToken()
    try {
      const resp = await fetch(`${API_BASE}/${card.clarify_id}/resume`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: 'Bearer ' + token } : {}),
        },
        body: JSON.stringify({
          response_type: responseType,
          selections: selections.value,
          free_text: freeText,
          confirmed,
        }),
      })
      const ctype = (resp.headers && resp.headers.get) ? (resp.headers.get('content-type') || '') : ''
      if (ctype.includes('event-stream')) {
        // Legacy backend answered the resume with a stream; let the
        // legacy view fallback handle it (re-run question).
        clearActiveCard()
        return { status: 'confirmed' }
      }
      const body = await resp.json()
      if (!resp.ok) {
        // 422 → parse failed, re-prompt; 409/404 → session issue.
        throw new Error(body.message || body.detail || `HTTP ${resp.status}`)
      }
      if (body.status === 'amended') {
        // Backend returned a new card for the next round.
        activeCard.value = {
          clarify_id: card.clarify_id,
          question: card.question,
          missing_fields: body.missing_fields || [],
          suggestions: body.suggestions || [],
          current_summary: body.merged_plan ? body.merged_plan.question : '',
        }
        selections.value = {}
        return { status: 'amended' }
      }
      if (body.status === 'cancelled') {
        clearActiveCard()
        return { status: 'cancelled' }
      }
      // Confirmed → SSE stream handled by useChat (not via this store).
      clearActiveCard()
      return { status: 'confirmed' }
    } catch (e) {
      error.value = e?.message || '提交失败'
      throw e
    } finally {
      submitting.value = false
    }
  }

  /**
   * Submit a free-text answer (LLM parses it into selections on the
   * backend, then resumes).
   */
  async function submitFreeText(text) {
    return submitResponse({ responseType: 'free_text', freeText: text, confirmed: true })
  }

  /** Cancel the session (abandon). */
  async function cancel() {
    return submitResponse({ responseType: 'cancel', confirmed: false })
  }

  /**
   * Submit a chosen multi-path candidate plan (多路重排).
   * Backend resolves the candidate_id to a full plan and resumes the graph.
   */
  async function submitCandidate(candidateId) {
    const card = activeCard.value
    if (!card || !card.clarify_id) throw new Error('No active clarify session')
    error.value = ''
    // Stage for the streamed inbox resume (same as confirmed path).
    pendingResponse.value = {
      response_type: 'candidate',
      candidate_id: candidateId,
      selections: { ...selections.value },
      free_text: '',
      confirmed: true,
    }
    clearActiveCard()
    return { status: 'candidate_confirmed' }
  }

  return {
    activeCard, selections, submitting, error, pendingResponse,
    setActiveCard, clearActiveCard, select, setGroupBy, takePendingResponse,
    submitResponse, submitFreeText, cancel, submitCandidate,
  }
})
