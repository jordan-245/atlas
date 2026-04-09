/**
 * Typed fetch wrapper for Atlas dashboard API.
 * Uses same-origin credentials (Basic Auth is handled by the browser on first 401).
 * 10 second timeout via AbortController.
 */
export async function get<T>(url: string): Promise<T> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 10_000)
  try {
    const response = await fetch(url, {
      credentials: 'same-origin',
      signal: controller.signal,
      headers: { Accept: 'application/json' },
    })
    if (!response.ok) {
      throw new Error(`HTTP ${response.status} ${response.statusText} on ${url}`)
    }
    return (await response.json()) as T
  } finally {
    clearTimeout(timeoutId)
  }
}
