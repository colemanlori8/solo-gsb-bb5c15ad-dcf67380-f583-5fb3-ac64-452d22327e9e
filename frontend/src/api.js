const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* ignore */ }
    throw new Error(detail)
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  listDocuments: () => request('/documents'),
  createDocument: (title, content) =>
    request('/documents', { method: 'POST', body: JSON.stringify({ title, content }) }),
  getDocument: (id) => request(`/documents/${id}`),
  deleteDocument: (id) => request(`/documents/${id}`, { method: 'DELETE' }),
  getCooccurrence: (id, codeIds = null) => {
    const qs = codeIds && codeIds.length ? `?${codeIds.map((c) => `codes=${c}`).join('&')}` : ''
    return request(`/documents/${id}/cooccurrence${qs}`)
  },

  listCodes: () => request('/codes'),
  createCode: (name, color) =>
    request('/codes', { method: 'POST', body: JSON.stringify({ name, color }) }),
  updateCode: (id, patch) =>
    request(`/codes/${id}`, { method: 'PUT', body: JSON.stringify(patch) }),
  deleteCode: (id) => request(`/codes/${id}`, { method: 'DELETE' }),

  previewCoding: (payload) =>
    request('/codings/preview', { method: 'POST', body: JSON.stringify(payload) }),
  createCoding: (payload) =>
    request('/codings', { method: 'POST', body: JSON.stringify(payload) }),
  updateCoding: (id, payload) =>
    request(`/codings/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteCoding: (id) => request(`/codings/${id}`, { method: 'DELETE' }),

  exportUrl: (docId) => `${BASE}/documents/${docId}/export`,
}
