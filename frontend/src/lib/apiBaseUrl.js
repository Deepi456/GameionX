export function resolveApiBaseUrl(configuredBaseUrl, hostname) {
  if (configuredBaseUrl) return configuredBaseUrl
  if (hostname === '127.0.0.1') return 'http://127.0.0.1:8000'
  return 'http://localhost:8000'
}
