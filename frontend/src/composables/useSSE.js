/**
 * Generic SSE consumer for POST + text/event-stream endpoints.
 * Shared by the main /api/query stream and the M3 resume streams
 * (clarify / approval / inbox respond) so they feed one chat transcript.
 */

export async function consumeSSE(response, onEvent) {
  if (!response.body) throw new Error('Stream response failed')
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const events = buffer.split('\n\n')
      buffer = events.pop()
      for (const evt of events) {
        const line = evt.trim()
        if (!line.startsWith('data:')) continue
        let data
        try { data = JSON.parse(line.replace(/^data:\s*/, '')) } catch { continue }
        if (data && typeof data === 'object') onEvent(data)
      }
    }
  } finally {
    try { reader.releaseLock() } catch { /* noop */ }
  }
}

export function sseHeaders() {
  const token = localStorage.getItem('token') || ''
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: 'Bearer ' + token } : {}),
  }
}

/** POST an SSE endpoint and consume it (for resume/respond streams). */
export async function postSSE(url, body, onEvent) {
  const resp = await fetch(url, { method: 'POST', headers: sseHeaders(), body: JSON.stringify(body || {}) })
  const ctype = resp.headers.get('content-type') || ''
  if (!ctype.includes('event-stream')) {
    // JSON outcome (amended card / error) - surface as a single event.
    let payload = null
    try { payload = await resp.json() } catch { payload = { error: `HTTP ${resp.status}` } }
    onEvent({ _json: payload, _status: resp.status })
    return
  }
  await consumeSSE(resp, onEvent)
}
