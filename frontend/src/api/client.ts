import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_URL ||
  `${window.location.protocol}//${window.location.hostname}:8000`

export const apiClient = axios.create({
  baseURL: `${API_BASE}/api/v1`,
  headers: { 'Content-Type': 'application/json' },
})

// Attach token to every request
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// Auto-refresh on 401
apiClient.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config
    if (error.response?.status === 401 && !original._retry) {
      original._retry = true
      const refresh = localStorage.getItem('refresh_token')
      if (refresh) {
        try {
          const { data } = await axios.post(`${API_BASE}/api/v1/auth/refresh`, {
            refresh_token: refresh,
          })
          localStorage.setItem('access_token', data.access_token)
          localStorage.setItem('refresh_token', data.refresh_token)
          // Sync new tokens into Zustand in-memory state (e.g. WebSocket reconnect uses it)
          const { useAuthStore } = await import('../stores/authStore')
          useAuthStore.setState((s) => ({
            ...s,
            accessToken: data.access_token,
            refreshToken: data.refresh_token,
          }))
          original.headers.Authorization = `Bearer ${data.access_token}`
          return apiClient(original)
        } catch {
          localStorage.removeItem('access_token')
          localStorage.removeItem('refresh_token')
          const { useAuthStore } = await import('../stores/authStore')
          useAuthStore.getState().logout()
          window.location.href = '/login'
        }
      }
    }
    return Promise.reject(error)
  }
)

// Auth
export const authApi = {
  register: (data: { email: string; username: string; password: string }) =>
    apiClient.post('/auth/register', data).then((r) => r.data),
  login: (data: { email: string; password: string }) =>
    apiClient.post('/auth/login', data).then((r) => r.data),
  me: () => apiClient.get('/auth/me').then((r) => r.data),
}

// Conversations
export const conversationsApi = {
  list: () => apiClient.get('/conversations').then((r) => r.data),
  create: (data?: { title?: string; model_id?: string }) =>
    apiClient.post('/conversations', data || {}).then((r) => r.data),
  get: (id: string) => apiClient.get(`/conversations/${id}`).then((r) => r.data),
  delete: (id: string) => apiClient.delete(`/conversations/${id}`),
  getMessages: (id: string) => apiClient.get(`/conversations/${id}/messages`).then((r) => r.data),
}

// Models
export const modelsApi = {
  list: () => apiClient.get('/models').then((r) => r.data),
  create: (data: object) => apiClient.post('/models', data).then((r) => r.data),
  delete: (id: string) => apiClient.delete(`/models/${id}`),
  test: (id: string) => apiClient.post(`/models/${id}/test`).then((r) => r.data),
}

// Datasources
export const datasourcesApi = {
  list: () => apiClient.get('/datasources').then((r) => r.data),
  create: (data: object) => apiClient.post('/datasources', data).then((r) => r.data),
  delete: (id: string) => apiClient.delete(`/datasources/${id}`),
  test: (id: string) => apiClient.post(`/datasources/${id}/test`).then((r) => r.data),
  schema: (id: string) => apiClient.get(`/datasources/${id}/schema`).then((r) => r.data),
}

// Knowledge
export const knowledgeApi = {
  list: () => apiClient.get('/knowledge').then((r) => r.data),
  upload: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return apiClient.post('/knowledge/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data)
  },
  addRepo: (data: { repo_url: string; branch?: string; access_token?: string; name?: string }) =>
    apiClient.post('/knowledge/repo', data).then((r) => r.data),
  status: (id: string) => apiClient.get(`/knowledge/${id}/status`).then((r) => r.data),
  delete: (id: string) => apiClient.delete(`/knowledge/${id}`),
}

// Skills
export const skillsApi = {
  list: () => apiClient.get('/skills').then((r) => r.data),
  create: (data: object) => apiClient.post('/skills', data).then((r) => r.data),
  get: (id: string) => apiClient.get(`/skills/${id}`).then((r) => r.data),
  update: (id: string, data: object) => apiClient.put(`/skills/${id}`, data).then((r) => r.data),
  delete: (id: string) => apiClient.delete(`/skills/${id}`),
}

// MCP
export const mcpApi = {
  list: () => apiClient.get('/mcp').then((r) => r.data),
  create: (data: object) => apiClient.post('/mcp', data).then((r) => r.data),
  delete: (id: string) => apiClient.delete(`/mcp/${id}`),
}

// Admin
export const adminApi = {
  listUsers: () => apiClient.get('/admin/users').then((r) => r.data),
  createUser: (data: object) => apiClient.post('/admin/users', data).then((r) => r.data),
  updateUser: (id: string, data: object) => apiClient.put(`/admin/users/${id}`, data).then((r) => r.data),
  deleteUser: (id: string) => apiClient.delete(`/admin/users/${id}`),
  getPermissions: (userId: string) =>
    apiClient.get(`/admin/users/${userId}/permissions`).then((r) => r.data),
  assignPermissions: (userId: string, data: object) =>
    apiClient.post(`/admin/users/${userId}/permissions`, data).then((r) => r.data),
  auditLogs: (skip = 0, limit = 100) =>
    apiClient.get(`/admin/audit-logs?skip=${skip}&limit=${limit}`).then((r) => r.data),
}

// Log sources
export const logsApi = {
  listSources: () => apiClient.get('/logs/sources').then((r) => r.data),
  createSource: (data: object) => apiClient.post('/logs/sources', data).then((r) => r.data),
  deleteSource: (id: string) => apiClient.delete(`/logs/sources/${id}`),
  search: (data: object) => apiClient.post('/logs/search', data).then((r) => r.data),
}
