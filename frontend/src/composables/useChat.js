import { ref, nextTick } from 'vue'
import { useHistory } from './useHistory.js'
import { useClarifyStore } from '../stores/clarify.js'
import { postSSE } from './useSSE.js'
import { useInboxStore } from '../stores/inbox.js'

const API_URL = '/api/query'

export function useChat() {
  const question = ref('')
  const loading = ref(false)
  const messages = ref([])
  const lastRequestId = ref('')

  function recentHistory() {
    return messages.value
      .filter(m => m.role === 'user' && typeof m.content === 'string')
      .map(m => m.content)
      .slice(-3)
  }
  const { addQuery } = useHistory()
  const clarifyStore = useClarifyStore()

  // ------- Conversation management (SQLBot-style sessions) -------
  const SESSIONS_KEY = 'askinsight_sessions'

  function loadSessions() {
    try { return JSON.parse(localStorage.getItem(SESSIONS_KEY) || '[]') } catch { return [] }
  }

  function persistSessions(list) {
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(list.slice(-20)))
  }

  function saveCurrentSession() {
    if (!messages.value.length) return
    const first = messages.value.find(m => m.role === 'user' && typeof m.content === 'string')
    const list = loadSessions()
    list.push({
      id: Date.now(),
      title: (first ? first.content : '新对话').slice(0, 24),
      at: new Date().toLocaleString('zh-CN'),
      messages: JSON.parse(JSON.stringify(messages.value)),
    })
    persistSessions(list)
  }

  function newConversation() {
    saveCurrentSession()
    messages.value = []
    lastRequestId.value = ''
  }

  function restoreSession(id) {
    const sess = loadSessions().find(x => x.id === id)
    if (!sess) return
    // archive current (if any) then replace
    saveCurrentSession()
    messages.value = sess.messages
    // drop the restored copy from the saved list (it is now live)
    persistSessions(loadSessions().filter(x => x.id !== id))
  }

  function deleteSession(id) {
    persistSessions(loadSessions().filter(x => x.id !== id))
  }

  function detectType(result) {
    if (!result || !result.length) return 'table'
    const keys = Object.keys(result[0])
    if (keys.includes('reply')) return 'reply'
    if (result.length === 1 && keys.length === 1) return 'card'
    const numericKeys = keys.filter(k => typeof result[0][k] === 'number' || /^-?\d/.test(String(result[0][k])))
    const categoryKeys = keys.filter(k => !numericKeys.includes(k))
    if (numericKeys.length >= 1 && categoryKeys.length >= 1 && result.length >= 2) return 'chart'
    return 'table'
  }

  function pushResultMessage(resultData, sqlText, spillId, totalRows) {
    const type = detectType(resultData)
    const spill = spillId ? { spillId, totalRows } : null
    if (type === 'reply') {
      messages.value.push({ role: 'assistant', type: 'reply', content: resultData[0].reply })
      return
    }
    if (type === 'card') {
      const k = Object.keys(resultData[0])[0]
      messages.value.push({ role: 'assistant', type: 'card', value: resultData[0][k], label: k })
      return
    }
    if (type === 'chart') {
      const keys = Object.keys(resultData[0])
      const numKeys = keys.filter(k => typeof resultData[0][k] === 'number' || /^-?\d/.test(String(resultData[0][k])))
      const catKeys = keys.filter(k => !numKeys.includes(k))
      messages.value.push({
        role: 'assistant', type: 'chart',
        rows: resultData,
        categoryKey: catKeys[0] || keys[0],
        valueKeys: numKeys,
        ...(spill || {}),
      })
      return
    }
    messages.value.push({
      role: 'assistant', type: 'table-with-sql', sql: sqlText,
      columns: Object.keys(resultData[0] || {}),
      rows: resultData,
      ...(spill || {}),
    })
  }

  /**
   * Shared SSE event reducer: appends steps / results / clarify /
   * approval cards to the chat transcript. Used by both the main
   * query stream and the M3 resume streams so one turn's transcript
   * stays in order no matter which endpoint drove it.
   */
  function makeStreamHandler(stepIdxRef, doneRef) {
    let resultData = null
    let sqlText = null
    let spillId = null
    let totalRows = 0
    let insightText = null
    let summaryData = null
    let isError = false
    let suspended = null
    let stageStartTime = Date.now()
    return {
      onEvent(data) {
        if (data._json) {
          // Non-SSE JSON outcome from a resume endpoint (amended card etc.)
          const payload = data._json
          const extra = payload && payload.extra ? payload.extra : payload
          if (payload && (payload.status === 'amended' || payload.status === 'parse_failed') && extra) {
            clarifyStore.setActiveCard({
              clarify_id: payload.clarify_id || (payload.item && payload.item.payload ? payload.item.payload.ref_id : ''),
              missing_fields: extra.missing_fields || [],
              suggestions: extra.suggestions || [],
              current_summary: extra.merged_plan ? extra.merged_plan.question : '',
            })
            messages.value.push({ role: 'assistant', type: 'clarify', card: { ...extra, clarify_id: payload.clarify_id } })
            suspended = 'clarify'
          } else if (payload && payload.status === 'parse_failed') {
            // Free-text parse failed: re-open the clarify card.
            clarifyStore.setActiveCard({
              clarify_id: payload.clarify_id,
              missing_fields: [],
              suggestions: [],
              current_summary: '',
            })
            messages.value.push({ role: 'assistant', type: 'reply', content: payload.message || '未能理解您的回复,请从选项中选择' })
            suspended = 'clarify'
          } else if (payload && payload.error) {
            messages.value.push({ role: 'assistant', type: 'error', content: payload.error })
            isError = true
          } else if (payload && payload.status && payload.status !== 'ok') {
            messages.value.push({ role: 'assistant', type: 'reply', content: payload.message || payload.status })
          }
          return
        }
        if (data.request_id && !data.stage) {
          lastRequestId.value = data.request_id
          return
        }
        // Hold the message OBJECT, not its index: sibling messages may
        // be spliced mid-stream (clarify card removal) which shifts
        // indices and silently drops every event.
        const steps = stepIdxRef.msg && stepIdxRef.msg.steps
        if (!steps) return
        if (data.stage) {
          const last = steps.at(-1)
          if (last && last.status === 'running') {
            last.status = 'success'
            last.time = Date.now() - stageStartTime
          }
          stageStartTime = Date.now()
          steps.push({ text: data.stage, status: 'running', time: 0 })
        } else if (data.clarify_required) {
          const last = steps.at(-1)
          if (last) { last.status = 'success'; last.time = Date.now() - stageStartTime }
          clarifyStore.setActiveCard(data.clarify_required)
          messages.value.push({ role: 'assistant', type: 'clarify', card: data.clarify_required })
          suspended = 'clarify'
        } else if (data.pending_approval) {
          const last = steps.at(-1)
          if (last) { last.status = 'success'; last.time = Date.now() - stageStartTime }
          messages.value.push({ role: 'assistant', type: 'approval', card: data.pending_approval })
          suspended = 'approval'
        } else if (data.error) {
          const last = steps.at(-1)
          if (last) last.status = 'error'
          messages.value.push({ role: 'assistant', type: 'error', content: data.error })
          isError = true
        } else if (Array.isArray(data.result)) {
          const last = steps.at(-1)
          if (last) { last.status = 'success'; last.time = Date.now() - stageStartTime }
          resultData = data.result
          if (data.sql) sqlText = data.sql
          if (data.spill_id) spillId = data.spill_id
          if (typeof data.total_rows === 'number') totalRows = data.total_rows
          if (data.summary && data.summary.text) summaryData = data.summary
        } else if (typeof data.decision_insights === 'string' && data.decision_insights.trim()) {
          insightText = data.decision_insights.trim()
        }
      },
      finish() {
        const steps = stepIdxRef.msg && stepIdxRef.msg.steps
        if (steps) {
          const last = steps.at(-1)
          if (last && last.status === 'running') { last.status = 'success'; last.time = 0 }
        }
        doneRef.v = { resultData, sqlText, spillId, totalRows, insightText, summaryData, isError, suspended }
      },
    }
  }

  /** Push an assistant steps placeholder; returns an object holder
   *  (index-free so array splices elsewhere can't break the stream). */
  function newStepsMessage() {
    const msg = { role: 'assistant', type: 'steps', steps: [] }
    const holder = { msg }
    messages.value.push(msg)
    return holder
  }

  async function sendQuestion(queryOverride) {
    const q = queryOverride || question.value
    if (!q || loading.value) return
    question.value = ''
    loading.value = true
    messages.value.push({ role: 'user', type: 'text', content: q })
    const stepIdx = newStepsMessage()
    await nextTick()
    const done = { v: null }
    const handler = makeStreamHandler(stepIdx, done)
    try {
      const resp = await fetch(API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(localStorage.getItem('token') ? { Authorization: 'Bearer ' + localStorage.getItem('token') } : {}) },
        body: JSON.stringify({ query: q, history: recentHistory() }),
      })
      if (resp.status === 429) {
        handler.onEvent({ type: 'error', error: '请求过于频繁（每分钟上限 10 次），请稍候再试' })
        handler.finish()
        return
      }
      if (resp.status === 401) {
        localStorage.removeItem('token')
        location.href = '/login'
        return
      }
      const { consumeSSE } = await import('./useSSE.js')
      await consumeSSE(resp, handler.onEvent)
      handler.finish()
      if (done.v && done.v.resultData && !done.v.isError) {
        const singleValue = done.v.resultData && done.v.resultData.length === 1
          if (done.v.summaryData && singleValue) {
            // 1x1 -> metric card only (value + sentence)
            messages.value.push({
              role: 'assistant', type: 'metric',
              metric: done.v.summaryData.metric,
              text: done.v.summaryData.text,
              value: done.v.summaryData.value,
              unit: done.v.summaryData.unit || '',
            })
          } else {
            // multi-row -> table/chart + facts note + suggestion chips
            pushResultMessage(done.v.resultData, done.v.sqlText, done.v.spillId, done.v.totalRows)
            if (done.v.summaryData && done.v.summaryData.text) {
              messages.value.push({
                role: 'assistant', type: 'note',
                content: done.v.summaryData.text,
                suggestions: done.v.summaryData.suggestions || [],
              })
            }
          }
          if (done.v.insightText) {
            messages.value.push({ role: 'assistant', type: 'insight', content: done.v.insightText })
          }
        addQuery(q)
      }
    } catch (e) {
      messages.value.push({ role: 'assistant', type: 'error', content: e?.message || 'Request failed' })
    } finally {
      loading.value = false
    }
  }

  /**
   * Drive an M3 resume/respond SSE stream into the chat transcript
   * (clarify confirm, approval decide, future kinds). One code path
   * replaces the old "re-send the whole question" workaround.
   */
  async function streamResume(url, body) {
    if (!url || loading.value) return
    loading.value = true
    const stepIdx = newStepsMessage()
    await nextTick()
    const done = { v: null }
    const handler = makeStreamHandler(stepIdx, done)
    try {
      await postSSE(url, body, handler.onEvent)
      handler.finish()
      if (done.v && done.v.resultData && !done.v.isError) {
        if (done.v.summaryData) {
            messages.value.push({
              role: 'assistant', type: 'metric',
              metric: done.v.summaryData.metric,
              text: done.v.summaryData.text,
              value: done.v.summaryData.value,
              unit: done.v.summaryData.unit || '',
            })
          } else {
            pushResultMessage(done.v.resultData, done.v.sqlText, done.v.spillId, done.v.totalRows)
          }
          if (done.v.insightText) {
            messages.value.push({ role: 'assistant', type: 'insight', content: done.v.insightText })
          }
      }
    } catch (e) {
      messages.value.push({ role: 'assistant', type: 'error', content: e?.message || 'Resume failed' })
    } finally {
      loading.value = false
    }
  }

  /** Clarify confirmed: resume via inbox respond URL when available,
   *  else fall back to the legacy re-run (older sessions). The staged
   *  payload comes from the clarify store (selections / free text /
   *  candidate pick). */
  function resumeClarify(card) {
    const url = card.respond_url || (card.inbox_id ? `/api/v1/inbox/${card.inbox_id}/respond` : null)
    if (!url) return false
    const staged = clarifyStore.takePendingResponse() || {
      response_type: 'selection', selections: {}, free_text: '', confirmed: true,
    }
    streamResume(url, { response: staged })
    return true
  }

  /** Approval decided: respond via inbox, streaming the continuation. */
  function resumeApproval(card, decision, note = '') {
    const url = card.respond_url || (card.inbox_id ? `/api/v1/inbox/${card.inbox_id}/respond` : null)
    if (!url) {
      // Legacy ticket flow: decide then resume, two requests.
      legacyApprovalFlow(card, decision, note)
      return
    }
    try { useInboxStore().removeById(card.inbox_id) } catch { /* pinia not ready */ }
    streamResume(url, { response: { decision, note } })
  }

  async function legacyApprovalFlow(card, decision, note) {
    const token = localStorage.getItem('token') || ''
    try {
      const resp = await fetch(`/api/approvals/${card.ticket_id}/decide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
        body: JSON.stringify({ decision, note }),
      })
      const body = await resp.json()
      if (!resp.ok) throw new Error(body.message || body.detail || `HTTP ${resp.status}`)
      if (decision === 'approved') {
        streamResume(`/api/approvals/${card.ticket_id}/resume`, {})
      } else {
        messages.value.push({ role: 'assistant', type: 'reply', content: '该查询已被拒绝执行。' })
      }
    } catch (e) {
      messages.value.push({ role: 'assistant', type: 'error', content: e?.message || '审批失败' })
    }
  }

  return {
    question, loading, messages, lastRequestId, sendQuestion,
    streamResume, resumeClarify, resumeApproval, detectType,
    newConversation, loadSessions, restoreSession, deleteSession,
  }
}
