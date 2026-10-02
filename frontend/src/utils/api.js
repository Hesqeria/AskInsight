const BASE = ''

async function request(url, options = {}) {
  const token = localStorage.getItem('token') || ''
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers,
  }
  const resp = await fetch(BASE + url, { ...options, headers })
  if (resp.status === 401) {
    localStorage.removeItem('token')
    window.location.href = '/login'
    throw new Error('Session expired')
  }
  return resp
}

export async function apiGet(url) {
  const resp = await request(url)
  return resp.json()
}

export async function apiPost(url, body) {
  const resp = await request(url, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return resp.json()
}

export async function apiStream(url, body) {
  const token = localStorage.getItem('token') || ''
  const resp = await fetch(BASE + url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  })
  if (!resp.ok) {
    if (resp.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    throw new Error(`HTTP ${resp.status}`)
  }
  return resp.body.getReader()
}
