const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').trim().replace(/\/+$/, '')

export function apiUrl(path) {
  const value = String(path)
  if (/^https?:\/\//i.test(value)) return value
  const normalizedPath = value.startsWith('/') ? value : `/${value}`
  return `${API_BASE_URL}${normalizedPath}`
}
