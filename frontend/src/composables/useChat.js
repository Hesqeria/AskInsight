import { ref, nextTick } from 'vue'
import { useHistory } from './useHistory.js'

const API_URL = '/api/query'

export function useChat() {
  const question = ref('')
  const loading = ref(false)
  const messages = ref([])
  const { addQuery } = useHistory()

  function getToken() {
    return localStorage.getItem('token') || ''
  }

  function detectType(result) {
    if (!result || !result.length) return 'table'
    const keys = Object.keys(result[0])
    if (keys.includes('reply')) return 'reply'
    if (result.length === 1 && keys.length === 1) return 'card'
    const numericKeys = keys.filter(k => typeof result[0][k] === 'number' || /^-?\\d/.test(String(result[0][k])))
    const categoryKeys = keys.filter(k => !numericKeys.includes(k))
    if (numericKeys.length >= 1 && categoryKeys.length >= 1 && result.length >= 2) return 'chart'
    return 'table'
  }

  async function sendQuestion(queryOverride) {
    const q = queryOverride || question.value
    if (!q || loading.value) return
    question.value = ''
    loading.value = true
    messages.value.push({ role: 'user', type: 'text', content: q })
    const stepIdx = messages.value.push({ role: 'assistant', type: 'steps', steps: [] }) - 1
    await nextTick()

    const token = getToken()
    try {
      const response = await fetch(API_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: 'Bearer ' + token } : {}),
        },
        body: JSON.stringify({ query: q, history: [] }),
      })
      if (!response.body) throw new Error('Stream response failed')

      const reader = response.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      let resultData = null
      let sqlText = null
      let isError = false
      let stageStartTime = Date.now()

      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const events = buffer.split('\\n\\n')
        buffer = events.pop()

        for (const evt of events) {
          const line = evt.trim()
          if (!line.startsWith('data:')) continue
          let data
          try { data = JSON.parse(line.replace(/^data:\\s*/, '')) } catch { continue }

          const steps = messages.value[stepIdx].steps
          if (data.stage) {
            const last = steps.at(-1)
            if (last && last.status === 'running') {
              last.status = 'success'
              last.time = Date.now() - stageStartTime
            }
            stageStartTime = Date.now()
            steps.push({ text: data.stage, status: 'running', time: 0 })
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
          }
        }
      }

      if (resultData && !isError) {
        const type = detectType(resultData)
        if (type === 'reply') {
          messages.value.push({ role: 'assistant', type: 'reply', content: resultData[0].reply })
        } else if (type === 'card') {
          const k = Object.keys(resultData[0])[0]
          messages.value.push({ role: 'assistant', type: 'card', value: resultData[0][k], label: k })
        } else if (type === 'chart') {
          const keys = Object.keys(resultData[0])
          const numKeys = keys.filter(k => typeof resultData[0][k] === 'number' || /^-?\\d/.test(String(resultData[0][k])))
          const catKeys = keys.filter(k => !numKeys.includes(k))
          messages.value.push({
            role: 'assistant', type: 'chart',
            rows: resultData,
            categoryKey: catKeys[0] || keys[0],
            valueKeys: numKeys,
          })
        } else {
          messages.value.push({
            role: 'assistant', type: 'table-with-sql', sql: sqlText,
            columns: Object.keys(resultData[0] || {}),
            rows: resultData,
          })
        }
        addQuery(q)
      }
    } catch (e) {
      messages.value.push({ role: 'assistant', type: 'error', content: e?.message || 'Request failed' })
    } finally {
      loading.value = false
    }
  }

  return { question, loading, messages, sendQuestion, detectType }
}
